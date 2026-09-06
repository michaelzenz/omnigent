"""Structural guards for the app.py / native-package split.

These tests exist because of a real incident: an upstream merge re-added the
inline function definitions that the runner's ``native/`` extraction had
deleted from ``app.py``, silently shadowing the maintained implementations
for weeks (~150 tests red, no error pointing at the cause).

Three invariants keep that class of failure impossible:

1. **No shadows** — every name ``app.py`` imports from ``omnigent.runner.native``
   must be undefined at app.py's top level. A local def of the same name wins
   over the import, resurrecting the stale upstream body.
2. **One-way dependency** — the ``native`` package must never import or lazily
   reference ``runner.app``. app → native only; a back edge is a load-time
   circular-import trap and a merge-conflict magnet.
3. **Single-homed state** — module-level mutable registries (forwarder task
   tables, app-server pools, …) must exist in exactly one module. Duplicate
   registries under the same name cause split-brain: one path registers in
   dict A, another cancels from dict B, and the session leaks a live task.
"""

from __future__ import annotations

import ast
import pathlib

RUNNER_DIR = pathlib.Path(__file__).resolve().parents[2] / "omnigent" / "runner"
APP_PATH = RUNNER_DIR / "app.py"
NATIVE_DIR = RUNNER_DIR / "native"


def _module_imports(tree: ast.Module) -> set[str]:
    """All names bound by top-level (and lazy) import statements."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _top_level_defs(tree: ast.Module) -> dict[str, ast.stmt]:
    """Name -> defining node for every top-level def/class/assignment."""
    defs: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defs[target.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defs[node.target.id] = node
    return defs


def test_app_imports_are_not_shadowed_by_local_definitions() -> None:
    """Every native-imported name must be undefined at app.py's top level.

    A top-level def of an imported name shadows the import. After the
    2026-08 upstream merge resurrected ~26 such shadows, the stale bodies
    silently replaced the maintained ``native/`` implementations and broke
    every caller using the new signatures.
    """
    tree = ast.parse(APP_PATH.read_text())
    defs = _top_level_defs(tree)
    native_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "omnigent.runner.native" or node.module.startswith(
                "omnigent.runner.native."
            ):
                for alias in node.names:
                    native_names.add(alias.asname or alias.name)
    shadows = sorted(native_names & set(defs))
    assert not shadows, (
        "app.py re-defines names it imports from omnigent.runner.native; "
        "the local definition shadows the maintained implementation: "
        f"{shadows}. Delete the local copies — the import must win."
    )


def test_native_package_never_imports_runner_app() -> None:
    """The dependency arrow is one-way: app.py -> native, never back.

    A back edge from native to app creates a load-time circular import
    (app imports native at module scope) and re-couples the leaf helpers
    to the machinery they must stay independent of.
    """
    offenders: list[str] = []
    for path in NATIVE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            mod = getattr(node, "module", "") or ""
            names = [a.name for a in getattr(node, "names", [])]
            hits = [
                n
                for n in names
                if n == "omnigent.runner.app" or n.startswith("omnigent.runner.app.")
            ]
            if mod == "omnigent.runner.app" or mod.startswith("omnigent.runner.app.") or hits:
                offenders.append(f"{path.relative_to(RUNNER_DIR)}: {ast.dump(node)[:80]}")
    assert not offenders, (
        f"omnigent/runner/native/ imports omnigent.runner.app (circular dependency): {offenders}"
    )


def test_native_source_has_no_lazy_runner_app_references() -> None:
    """No string or getattr reference to runner.app hides inside native code.

    Module-level import scanning (previous test) misses
    ``importlib.import_module("...runner.app")`` and dotted strings used by
    dynamic dispatch. This is a cheap textual net over the same invariant.
    """
    offenders: list[str] = []
    for path in NATIVE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        # Exclude docstring line ranges - prose may cite :mod:`runner.app`.
        doc_ranges: list[tuple[int, int]] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                doc_ranges.append(
                    (node.body[0].lineno, node.body[0].end_lineno or node.body[0].lineno)
                )
        text = path.read_text()
        for needle in ("runner.app", "runner import app"):
            if needle not in text:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if needle in line and not stripped.startswith("#"):
                    if any(lo <= lineno <= hi for lo, hi in doc_ranges):
                        continue
                    offenders.append(f"{path.name}:{lineno}: {stripped[:80]}")
    assert not offenders, f"native/ references runner.app dynamically: {offenders}"


def test_mutable_registries_are_single_homed() -> None:
    """State dicts/sets shared by register/cancel/teardown paths must be unique.

    The forwarder-registry split-brain: app.py kept its own
    ``_AUTO_FORWARDER_TASKS`` while ``_cancel_auto_forwarder_task`` resolved
    to the native copy reading a different dict — sessions leaked live
    forwarder tasks. Any name defined as a module-level dict/set in BOTH
    app.py and a native module is suspect.
    """
    app_defs = _top_level_defs(ast.parse(APP_PATH.read_text()))

    def mutable_container(node: ast.stmt) -> bool:
        if isinstance(node, ast.Assign) and node.value is not None:
            return isinstance(node.value, (ast.Dict, ast.DictComp, ast.Set, ast.SetComp))
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            return isinstance(node.value, (ast.Dict, ast.DictComp, ast.Set, ast.SetComp))
        return False

    app_registries = {n for n, node in app_defs.items() if mutable_container(node)}

    duplicated: list[str] = []
    for path in NATIVE_DIR.rglob("*.py"):
        ntree = ast.parse(path.read_text())
        for name in _top_level_defs(ntree):
            if name in app_registries:
                duplicated.append(f"{name} (app.py + {path.name})")
    assert not duplicated, (
        "mutable registries defined in both app.py and native/ — "
        f"register/cancel paths will split-brain: {duplicated}. "
        "Keep the registry and its register/cancel functions in one module."
    )


def test_native_launch_machinery_is_owned_by_orchestration() -> None:
    """Native launch entry points stay in orchestration, never app.py."""
    app_defs = _top_level_defs(ast.parse(APP_PATH.read_text()))
    orchestration_defs = _top_level_defs(ast.parse((NATIVE_DIR / "orchestration.py").read_text()))
    expected = {
        "ResolvedSpec",
        "_auto_create_claude_terminal",
        "_auto_create_codex_terminal",
        "_codex_discover_thread_and_forward",
        "_launch_claude",
        "_launch_codex",
        "_launch_native_terminal",
    }
    assert expected <= set(orchestration_defs), (
        "native/orchestration.py is missing required launch machinery: "
        f"{sorted(expected - set(orchestration_defs))}"
    )
    assert not expected & set(app_defs), (
        "app.py must delegate native launch machinery to native/orchestration.py: "
        f"{sorted(expected & set(app_defs))}"
    )


def test_native_provider_hooks_target_native_package() -> None:
    """Provider lazy-import strings must resolve launch hooks from native/."""
    provider_source = (RUNNER_DIR.parent / "harness_plugins.py").read_text()
    assert 'auto_create_terminal=f"omnigent.runner.native:_launch_{key}"' in provider_source
    assert 'auto_create_terminal=f"omnigent.runner.app:_launch_{key}"' not in provider_source
