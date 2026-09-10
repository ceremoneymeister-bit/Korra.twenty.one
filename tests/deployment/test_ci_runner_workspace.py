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


@pytest.mark.parametrize("child_exit", [0, 37])
def test_python_ci_uses_private_home_and_tmp_and_preserves_exit(tmp_path, child_exit):
    """Execute the actual workflow; replace only its privilege/namespace boundary."""
    if not any(os.access(path, os.X_OK) for path in (
        "/usr/bin/tini", "/usr/bin/docker-init", "/usr/libexec/docker/docker-init"
    )):
        pytest.skip("Native reaping init is installed on the Linux CI runner")
    bindir = tmp_path / "bin"
    for name, boundary in (("sudo", "sh"), ("setpriv", "env")):
        executable(bindir / name, "#!/bin/sh\n"
                   f'while [ "$1" != "{boundary}" ]; do shift; done\nexec "$@"\n')
    repo = tmp_path / "repo"
    (repo / ".venv/bin").mkdir(parents=True)
    (repo / ".venv/bin/activate").write_text("", encoding="utf-8")
    executable(repo / "scripts/run_tests.sh", """#!/usr/bin/env python3
import json, os, pathlib, tempfile
home = pathlib.Path(os.environ["HOME"])
tmp = pathlib.Path(tempfile.gettempdir())
voice = tmp / "hermes_voice" / "synthetic.ogg"
voice.parent.mkdir(exist_ok=True)
voice.write_bytes(b"synthetic")
print(json.dumps(dict(home=str(home), tmp=str(tmp), uid=os.getuid(),
                     owner=home.parent.stat().st_uid,
                     mode=home.parent.stat().st_mode & 0o777,
                     voice=voice.read_bytes().decode())))
raise SystemExit(int(os.environ["SYNTHETIC_CHILD_EXIT"]))
""")
    inherited_home = tmp_path / "inherited-home"
    inherited_tmp = tmp_path / "inherited-tmp"
    inherited_home.mkdir()
    inherited_tmp.mkdir()
    env = {**os.environ, "HOME": str(inherited_home), "TMPDIR": str(inherited_tmp),
           "PATH": str(bindir) + ":" + os.environ["PATH"], "CI": "true",
           "GITHUB_ACTIONS": "true", "HERMES_TEST_WORKERS": "8",
           "SYNTHETIC_CHILD_EXIT": str(child_exit)}
    body = workflow_step("tests.yml", "test", "Run tests")
    result = subprocess.run(["bash", "-eu", "-o", "pipefail", "-c", body],
                            cwd=repo, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == child_exit, result.stderr
    observed = json.loads(result.stdout)
    private_home, private_tmp = Path(observed["home"]), Path(observed["tmp"])
    assert private_home != inherited_home
    assert private_tmp != inherited_tmp
    assert private_home.parent == private_tmp.parent
    assert observed["owner"] == observed["uid"]
    assert observed["mode"] == 0o700
    assert observed["voice"] == "synthetic"
    # The step removes only its newly created private root, even on test failure.
    assert not private_home.parent.exists()
    assert list(inherited_home.iterdir()) == []
    assert list(inherited_tmp.iterdir()) == []
