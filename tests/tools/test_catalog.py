from omnigent.tools.catalog import GROUPS, TOOLS


def test_pi_and_legacy_file_tools_have_separate_groups() -> None:
    groups = {group.id: group for group in GROUPS}
    assert groups["pi_file_interaction"].title == "File interaction — Pi"
    assert groups["legacy_os_interaction"].title == "Legacy File Interaction"
    by_group: dict[str, set[str]] = {}
    for tool in TOOLS:
        by_group.setdefault(tool.group, set()).add(tool.name)
    assert by_group["pi_file_interaction"] == {
        "read",
        "write",
        "edit",
        "bash",
        "grep",
        "find",
        "ls",
    }
    assert by_group["legacy_os_interaction"] == {
        "sys_os_read",
        "sys_os_write",
        "sys_os_edit",
        "sys_os_shell",
    }
