from omnigent.tools.catalog import GROUPS, TOOLS


def test_legacy_file_tools_group_and_no_pi_group() -> None:
    """The bridged Pi-style file tools are removed (pi natives serve the names).

    The catalog keeps only the legacy ``sys_os_*`` group; the
    ``pi_file_interaction`` group and its seven bridged tools
    (read/write/edit/bash/grep/find/ls) are gone — pi's native builtins
    (enabled by default, native-wins) provide those capabilities, and
    claude/codex native sessions use their harness-native tools.
    """
    groups = {group.id: group for group in GROUPS}
    assert "pi_file_interaction" not in groups
    assert groups["legacy_os_interaction"].title == "Legacy File Interaction"
    by_group: dict[str, set[str]] = {}
    for tool in TOOLS:
        by_group.setdefault(tool.group, set()).add(tool.name)
    assert "pi_file_interaction" not in by_group
    assert by_group["legacy_os_interaction"] == {
        "sys_os_read",
        "sys_os_write",
        "sys_os_edit",
        "sys_os_shell",
    }
    # The removed names must not reappear under any other group.
    all_names = {tool.name for tool in TOOLS}
    assert not all_names & {"bash", "grep", "find", "ls"}
