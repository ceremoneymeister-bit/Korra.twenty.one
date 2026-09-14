"""Exercise the shipped login-shell fragment without modifying host PATH."""

import os
from pathlib import Path
import shlex
import subprocess

import pytest


@pytest.mark.skipif(os.name == "nt", reason="Linux container login-shell fragment")
def test_runtime_path_restored_idempotently_without_losing_existing_entries():
    fragment = Path(__file__).resolve().parents[1] / "docker/runtime-path.sh"
    command = f'. {shlex.quote(str(fragment))}; . {shlex.quote(str(fragment))}; printf "%s" "$PATH"'
    original = "/custom/bin:/usr/local/bin:/usr/bin:/bin"
    result = subprocess.run(["/bin/sh", "-c", command], env={"PATH": original}, capture_output=True, text=True, check=True)
    assert result.stdout == "/opt/hermes/bin:/opt/hermes/.venv/bin:/opt/data/.local/bin:" + original
