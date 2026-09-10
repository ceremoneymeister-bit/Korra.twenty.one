"""K21-016: execute CI shell/Node boundaries with isolated fake executables."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="native Linux self-hosted CI shell")


def workflow_step(name, job, step):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
    return next(item["run"] for item in workflow["jobs"][job]["steps"] if item.get("name") == step)


def executable(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize("docker_exit,expected", [(0, 0), (1, 0), (125, 125), (126, 126), (127, 127), (137, 137)])
def test_scanner_findings_are_advisory_but_container_failure_is_not(tmp_path, docker_exit, expected):
    bindir = tmp_path / "bin"
    executable(bindir / "docker", "#!/bin/sh\nexit \"$SYNTHETIC_DOCKER_EXIT\"\n")
    env = {**os.environ, "PATH": str(bindir) + ":" + os.environ["PATH"],
           "HOME": str(tmp_path), "GITHUB_WORKSPACE": str(tmp_path),
           "SYNTHETIC_DOCKER_EXIT": str(docker_exit)}
    body = workflow_step("osv-scanner.yml", "scan", "Scan lockfiles (advisory)")
    result = subprocess.run(["bash", "-eu", "-o", "pipefail", "-c", body], env=env,
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == expected
    assert ("::warning::" in result.stdout) == (docker_exit == 1)


def test_nonlogin_rust_step_loads_existing_toolchain_and_preserves_failure(tmp_path):
    cargo = tmp_path / ".cargo" / "bin" / "cargo"
    executable(cargo, "#!/bin/sh\nprintf '%s\\n' \"$*\"\nexit 42\n")
    (tmp_path / ".cargo" / "env").write_text('export PATH="$HOME/.cargo/bin:$PATH"\n', encoding="utf-8")
    env = {**os.environ, "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    body = workflow_step("rust-tests.yml", "bootstrap-installer", "cargo test")
    result = subprocess.run(["bash", "-eu", "-o", "pipefail", "-c", body], env=env,
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 42
    assert result.stdout.strip() == "test --lib"


@pytest.mark.parametrize("empty", [False, True])
def test_failed_workspace_checks_flush_diagnostics_without_false_success(tmp_path, empty):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is installed by the native JS CI lane")
    bindir = tmp_path / "bin"
    executable(bindir / "npm", """#!/usr/bin/env python3
import json, os, sys
if sys.argv[1] == "query":
    print(json.dumps([] if os.environ["SYNTHETIC_EMPTY"] == "1" else [
        {"location": "synthetic-web", "scripts": {"check": "synthetic"}}]))
else:
    sys.stdout.write("x" * 200000 + "\\nSYNTHETIC_FAILURE_TAIL\\n")
    sys.exit(1)
""")
    env = {**os.environ, "HOME": str(tmp_path),
           "PATH": str(bindir) + ":" + os.environ["PATH"], "GITHUB_ACTIONS": "true",
           "SYNTHETIC_EMPTY": "1" if empty else "0"}
    result = subprocess.run([node, str(ROOT / ".github/scripts/run-workspace-checks.mjs"), "--concurrency", "1"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert "checks passed" not in result.stdout
    if empty:
        assert "No workspace package declares a check script" in result.stderr
    else:
        assert result.stdout.count("x") >= 200000
        assert "SYNTHETIC_FAILURE_TAIL" in result.stdout
        assert "=== summary ===" in result.stdout
        assert "synthetic-web :: check failed" in result.stderr
