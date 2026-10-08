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
    assert result.stdout == "/opt/hermes/bin:/opt/hermes/.venv/bin:/opt/data/.local/bin:/opt/data/lazy-packages/bin:" + original


def _dockerfile():
    return (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()


def test_dockerfile_path_matches_the_login_shell_fragment():
    line = next(l for l in _dockerfile().splitlines() if l.startswith('ENV PATH="/opt/hermes/bin'))
    assert line == 'ENV PATH="/opt/hermes/bin:/opt/hermes/.venv/bin:/opt/data/.local/bin:/opt/data/lazy-packages/bin:${PATH}"'


@pytest.mark.skipif(os.name == "nt", reason="Linux interpreter path semantics")
def test_durable_packages_pth_from_the_dockerfile_exposes_user_packages_after_core(tmp_path):
    import re
    import sys

    run = next(l for l in _dockerfile().splitlines() if "korra-durable-packages.pth" in l)
    name = re.search(r"'(korra-durable-packages\.pth)'", run).group(1)
    content = re.search(r"write_text\('([^']*)'\)", run).group(1).replace("\\\\", "\\").encode().decode("unicode_escape")
    assert content == "/opt/data/lazy-packages\n"
    store, site_dir = tmp_path / "lazy-packages", tmp_path / "site-packages"
    for directory in (store / "userpkg", site_dir / "corepkg", store / "corepkg"):
        directory.mkdir(parents=True)
    (store / "userpkg/__init__.py").write_text("WHO = 'user'\n")
    (site_dir / "corepkg/__init__.py").write_text("WHO = 'core'\n")
    (store / "corepkg/__init__.py").write_text("WHO = 'user'\n")
    (site_dir / name).write_text(content.replace("/opt/data/lazy-packages", str(store)))
    code = "import site, sys; site.addsitedir(sys.argv[1]); import userpkg, corepkg; print(userpkg.WHO, corepkg.WHO)"
    result = subprocess.run([sys.executable, "-S", "-c", code, str(site_dir)], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "user core"
