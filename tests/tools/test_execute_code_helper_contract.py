"""Published helper instructions work in real local and file-RPC kernels."""

import json
import re
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from tools.code_execution_tool import build_execute_code_schema, execute_code
from tools.code_kernel import shutdown_all_kernels
from tools.code_kernel_remote import shutdown_all_remote_kernels


class ShellBackend:
    """Exercise the remote protocol through real shell commands in a temp dir."""

    def __init__(self, root):
        self.root = root

    def get_temp_dir(self):
        return str(self.root)

    def execute(self, command, cwd=None, timeout=None):
        # A detached shell child may retain stdout. A file lets the transport
        # return as soon as the foreground shell exits, like an SSH backend.
        with tempfile.TemporaryFile(mode="w+") as output:
            done = subprocess.run(
                ["bash", "-c", command], cwd=cwd, timeout=timeout,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            )
            output.seek(0)
            return {"output": output.read(), "returncode": done.returncode}


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.linux_only
def test_published_helper_import_works_in_kernel(tmp_path, monkeypatch, remote):
    monkeypatch.setenv("TERMINAL_ENV", "ssh" if remote else "local")
    schema = build_execute_code_schema(mode="strict")
    instruction = re.search(r"Helpers: `([^`]+)`", schema["description"])
    if "Built-in helpers (no import)" in schema["description"]:
        helper_import = ""  # Follow the old published instruction, too.
    else:
        assert instruction, "the schema must teach the helper import"
        helper_import = instruction.group(1)
    values = ["two words", "quote'and\"double", "папка с пробелами", "$(echo bad); *"]
    code = helper_import + "\n" + (
        "import json, subprocess\n"
        f"values = {values!r}\n"
        "for value in values:\n"
        "    out = subprocess.check_output('printf %s ' + shell_quote(value), shell=True, text=True)\n"
        "    assert out == value, (out, value)\n"
        "assert json_parse('\ufeff{\"ok\": true}')[\"ok\"]\n"
        "assert retry(lambda: 42, delay=0) == 42\n"
        "print(json.dumps(values, ensure_ascii=False))\n"
    )
    try:
        with patch("tools.code_execution_tool._load_config", return_value={"mode": "strict", "timeout": 30}), \
             patch("tools.code_execution_tool._get_or_create_env", return_value=(ShellBackend(tmp_path), "ssh")):
            result = json.loads(execute_code(code, task_id="helper-contract"))
        assert result["status"] == "success", result
        assert json.loads(result["output"].strip()) == values
        assert result["kernel"]["execution_count"] == 1
        assert bool(result["kernel"].get("remote")) == remote
    finally:
        shutdown_all_kernels()
        shutdown_all_remote_kernels()


@pytest.mark.parametrize("helper, expression", [
    ("shell_quote", "shell_quote('путь с пробелами')"),
    ("json_parse", "json_parse('{}')"),
    ("retry", "retry(lambda: 42)"),
])
def test_error_hint_recovers_with_import(monkeypatch, helper, expression):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    try:
        bad = json.loads(execute_code(f"print({expression})", task_id="helper-hint", reset=True))
        assert bad["status"] == "error"
        instruction = re.search(r"`([^`]+)`", bad["hint"]).group(1)
        fixed = json.loads(execute_code(instruction + f"\nprint({expression})", task_id="helper-hint"))
        assert fixed["status"] == "success", fixed
    finally:
        shutdown_all_kernels()
