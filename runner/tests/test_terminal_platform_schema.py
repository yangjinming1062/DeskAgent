import json
import sys

import pytest
from tools import discover_builtin_tools
from tools.terminal import terminal_tool as terminal_tool_module


@pytest.fixture(scope="module", autouse=True)
def _discover_tools():
    discover_builtin_tools()


@pytest.mark.parametrize(
    ("platform_name", "platform_markers"),
    [
        ("win32", ("Windows host", "Git Bash", "C:\\Users\\name", "/c/Users/name", "winget")),
        ("darwin", ("macOS (Darwin) host", "/Users/name", "brew", "sed -i ''")),
    ],
)
def test_local_terminal_schema_is_host_specific(platform_name, platform_markers):
    description = terminal_tool_module.build_terminal_tool_description(platform_name)
    schema = terminal_tool_module.build_terminal_schema(platform_name, env_type="local")

    assert all(marker in description for marker in platform_markers)
    assert "not a virtual machine" in description
    assert "Set background=true" in description
    assert schema["description"] == description
    assert schema["parameters"]["properties"]["command"]["description"].startswith("The shell command to execute on the user's ")


@pytest.mark.parametrize(
    ("env_type", "environment_marker"),
    [
        ("docker", "configured Linux container"),
        ("singularity", "configured Linux container"),
        ("ssh", "configured remote host"),
    ],
)
def test_non_local_terminal_schema_describes_actual_backend(env_type, environment_marker):
    schema = terminal_tool_module.build_terminal_schema(env_type=env_type)

    assert environment_marker in schema["description"]
    assert "the user's Windows host" not in schema["description"]
    assert "the user's macOS host" not in schema["parameters"]["properties"]["command"]["description"]


@pytest.mark.parametrize(
    ("platform_name", "command", "expected_fragment"),
    [
        ("win32", "cd . && sudo -E apt-get install -y ripgrep", "'apt-get' is unavailable"),
        ("win32", "cd . && /usr/bin/systemctl status nginx", "systemctl is unavailable on this Windows host"),
        ("darwin", "cd . && pacman -S ripgrep", "macOS host"),
        ("darwin", "cd . && sudo systemctl status nginx", "systemctl is unavailable on this macOS host"),
    ],
)
def test_linux_commands_are_blocked_on_local_host(platform_name, command, expected_fragment, monkeypatch):
    monkeypatch.setattr(sys, "platform", platform_name)
    monkeypatch.setattr(terminal_tool_module, "get_env_config", lambda: {"env_type": "local"})

    result = json.loads(terminal_tool_module.terminal_tool(command=command, task_id="terminal-platform-test"))

    assert result["status"] == "blocked"
    assert expected_fragment in result["error"]


@pytest.mark.parametrize(
    "safe_command",
    [
        'python3 -c "apt = 1; print(apt)"',
        "echo 'systemctl status nginx'",
        'git commit -m "add pacman support"',
    ],
)
def test_quoted_linux_keywords_are_not_blocked(safe_command):
    assert terminal_tool_module._blocked_host_command_error(safe_command, "local", "win32") is None
    assert terminal_tool_module._blocked_host_command_error(safe_command, "local", "darwin") is None


@pytest.mark.parametrize("env_type", ["docker", "ssh"])
def test_linux_commands_are_not_blocked_for_non_local_environments(env_type):
    assert terminal_tool_module._blocked_host_command_error("apt-get install ripgrep", env_type) is None
    assert terminal_tool_module._blocked_host_command_error("systemctl status nginx", env_type) is None
