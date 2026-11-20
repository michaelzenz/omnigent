"""Runtime source of truth for the omnigent version.

``VERSION`` is the version string the runtime imports directly — the CLI
(``--version``), the server's ``/api/version`` endpoint, and the
host/runner ``hello`` frames all read this same value. Importing the
constant (rather than reading ``importlib.metadata``) means the version is
correct regardless of how the package was installed.

This constant mirrors the canonical ``[project].version`` in
``pyproject.toml``; a pre-commit hook (``scripts/sync_version_py.py``) keeps
the two in sync, so releases are cut by bumping pyproject alone (via
``scripts/update_versions.py``).
"""

VERSION = "0.23.0"


def version_line() -> str:
    """Render the line that ``omnigent --version`` prints for this build.

    Always includes the package version. When the build hook in
    ``setup.py`` wrote ``omnigent/_build_info.py``, the line also names the
    short commit SHA and the build time in ISO-8601 UTC. A source checkout
    that was never built prints only the bare version. A host daemon records
    this line, so it identifies the build that the daemon runs.

    :returns: Either ``"omnigent 0.1.0"`` (no build info), or
        ``"omnigent 0.1.0 (010cf77c, built 2026-05-21T14:34:45Z)"``.
    """
    import datetime

    from omnigent.update_check import _read_build_info

    info = _read_build_info()
    if info is None:
        return f"omnigent {VERSION}"
    epoch, sha = info
    when = datetime.datetime.fromtimestamp(epoch, tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if sha:
        # Short SHA (first 8 chars) — enough to disambiguate in bug
        # reports without making the line unwieldy.
        return f"omnigent {VERSION} ({sha[:8]}, built {when})"
    # _build_info exists but has no SHA (built without git available).
    return f"omnigent {VERSION} (built {when})"
