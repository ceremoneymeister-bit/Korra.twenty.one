"""The weekly-copy script of the drive-backup skill must be where cron looks for it."""

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2] / "skills" / "ops" / "drive-backup" / "SKILL.md"


def _script_block() -> str:
    text = SKILL.read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)```", text, re.DOTALL)
    (block,) = [b for b in blocks if "SCRIPTS_DIR" in b]
    return textwrap.dedent(block)


@pytest.mark.parametrize("profile", [False, True])
def test_skill_script_is_found_by_the_scheduler(tmp_path, monkeypatch, profile):
    real_home = tmp_path / "account-home"
    data = tmp_path / "opt-data"
    hermes_home = data / "profiles" / "work" if profile else data
    for directory in (real_home, hermes_home):
        directory.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_korra = bin_dir / "korra"
    fake_korra.write_text('#!/bin/bash\necho "fake korra $*"\n')
    fake_korra.chmod(0o755)

    # The agent terminal: HERMES_HOME of the active profile, HOME elsewhere
    # (in Docker it is {HERMES_HOME}/home or the account home).
    terminal_env = {**os.environ, "HERMES_HOME": str(hermes_home), "HOME": str(real_home / "home")}
    result = subprocess.run(["bash", "-c", _script_block()], env=terminal_env,
                            capture_output=True, text=True, check=True)
    created = Path(result.stdout.strip().splitlines()[-1])
    assert created == hermes_home / "scripts" / "korra-backup-weekly.sh"
    assert created.is_file()
    assert not (real_home / "home" / ".hermes").exists()

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    from cron.scheduler import _run_job_script

    ok, output = _run_job_script("korra-backup-weekly.sh")
    assert ok, output
    assert "fake korra backup --to-drive" in output


def test_cron_job_for_the_skill_script_can_be_created(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from cron.jobs import get_job
    from gateway.session_context import reset_session_vars, set_session_vars
    from tools.cronjob_tools import cronjob

    set_session_vars(platform="telegram", chat_id="900000001", user_id="900000001",
                     chat_type="dm", owner_principal="owner")
    try:
        result = json.loads(cronjob(action="create", name="Резервная копия на Google Диск",
                                    schedule="0 3 * * 0", script="korra-backup-weekly.sh",
                                    no_agent=True, deliver="origin"))
    finally:
        reset_session_vars()
    assert result["success"], result
    job = get_job(result["job_id"])
    assert job["enabled"] and job["no_agent"] and job["next_run_at"]
    assert job["script"] == "korra-backup-weekly.sh"


def test_skill_tells_to_verify_with_the_scheduler():
    text = SKILL.read_text(encoding="utf-8")
    assert "~/.hermes/scripts" not in text
    assert "action=run" in text and "Script not found" in text
