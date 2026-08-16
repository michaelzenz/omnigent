"""Backing-profile bundle helpers for glossary roles.

A role's ``agent_profile_id`` points at a *backing* agent — a private fork
whose bundle the role owns (deleted when the role is deleted). The prompt
textarea in the role form edits this bundle in place; Import copies a
packaged agent's spec into it. These helpers build/rewrite those bundles
without going through the full agent-bundle upload path.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import yaml

from omnigent.agent_tasks.agent_builtins import task_agent_spec_path
from omnigent.spec import materialize_bundle


def _tar_gz_dir(bundle_dir: Path) -> bytes:
    """Pack a bundle dir into a deterministic gzipped tarball (mirrors app.py)."""
    import gzip
    import io

    buf = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w") as tf,
    ):
        tf.add(str(bundle_dir), arcname=".")
    return buf.getvalue()


def _find_omnigent_yaml(root: Path) -> Path | None:
    """Return the single omnigent YAML in *root*, or ``None``."""
    from omnigent.spec import is_omnigent_yaml

    if (root / "config.yaml").exists():
        return None
    candidates = [p for p in root.iterdir() if p.is_file() and is_omnigent_yaml(p)]
    return candidates[0] if len(candidates) == 1 else None


def _set_yaml_prompt(doc: dict[str, Any], prompt: str) -> None:
    """Set the system-prompt key on a parsed omnigent YAML doc.

    The key may be ``prompt`` (omnigent) or ``instructions`` (cross-format
    alias); write back through whichever is present, defaulting to ``prompt``.
    """
    if "instructions" in doc and "prompt" not in doc:
        doc["instructions"] = prompt
    else:
        doc["prompt"] = prompt


def read_agent_prompt(artifact_store: Any, bundle_location: str) -> str | None:
    """Return the ``prompt`` text stored in an agent's bundle, or ``None``."""
    bundle_bytes = artifact_store.get(bundle_location)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:gz") as tf:
            tf.extractall(tmp_path, filter="data")
        yaml_path = _find_omnigent_yaml(tmp_path)
        if yaml_path is None:
            return None
        doc = yaml.safe_load(yaml_path.read_text()) or {}
        if not isinstance(doc, dict):
            return None
        value = doc.get("prompt")
        if value is None:
            value = doc.get("instructions")
        return value if isinstance(value, str) else None


def build_backing_bundle_from_packaged(
    source_name: str,
    *,
    fork_name: str,
    prompt_override: str | None = None,
) -> bytes:
    """Build a backing-fork bundle from a packaged agent YAML.

    Copies the packaged spec, rewrites the name to the fork's name (the
    agent row name is immutable), and optionally replaces the prompt. Used
    both for new-role seeding (``prompt_override=""`` for custom roles) and
    for Import-in-place (``prompt_override=None`` keeps the source prompt).
    """
    spec_path = task_agent_spec_path(source_name)
    with tempfile.TemporaryDirectory() as tmp:
        out_yaml = Path(tmp) / spec_path.name
        doc = yaml.safe_load(spec_path.read_text()) or {}
        if not isinstance(doc, dict):
            raise ValueError(f"packaged agent {source_name!r} is not a YAML mapping")
        doc["name"] = fork_name
        if prompt_override is not None:
            _set_yaml_prompt(doc, prompt_override)
        out_yaml.write_text(yaml.safe_dump(doc, sort_keys=False))
        bundle_dir = materialize_bundle(out_yaml, Path(tmp) / "bundle")
        return _tar_gz_dir(bundle_dir)


def rewrite_agent_prompt(
    artifact_store: Any,
    bundle_location: str,
    *,
    new_prompt: str,
) -> bytes:
    """Rebuild an agent's bundle with a new prompt, preserving everything else.

    Extracts the current bundle, rewrites the ``prompt`` field on its YAML,
    and re-materializes. Used for the role-form prompt textarea on a fork.
    """
    bundle_bytes = artifact_store.get(bundle_location)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:gz") as tf:
            tf.extractall(tmp_path, filter="data")
        yaml_path = _find_omnigent_yaml(tmp_path)
        if yaml_path is None:
            raise ValueError("backing bundle has no omnigent YAML to rewrite")
        doc = yaml.safe_load(yaml_path.read_text()) or {}
        if not isinstance(doc, dict):
            raise ValueError("backing bundle YAML is not a mapping")
        _set_yaml_prompt(doc, new_prompt)
        yaml_path.write_text(yaml.safe_dump(doc, sort_keys=False))
        bundle_dir = materialize_bundle(tmp_path, Path(tmp) / "bundle")
        return _tar_gz_dir(bundle_dir)
