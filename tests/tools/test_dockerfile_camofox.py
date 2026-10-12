"""K21-336: образ содержит закреплённый Camofox, а s6-сервис стартует только по включению."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "Dockerfile"
S6 = ROOT / "docker" / "s6-rc.d"


def _block() -> str:
    text = DOCKERFILE.read_text()
    start = text.index("ARG CAMOUFOX_RELEASE=")
    end = text.index("# ---------- Frontend build", start)
    return text[start:end]


def test_camofox_layer_precedes_the_source_copy_and_frontend() -> None:
    text = DOCKERFILE.read_text()
    layer = text.index("ARG CAMOUFOX_RELEASE=")
    assert layer < text.index("COPY web/ web/")
    assert layer < text.index("\nCOPY --link --chmod=a+rX,go-w . .")


def test_npm_package_is_pinned_with_lock_outside_the_root_package_json() -> None:
    package = json.loads((ROOT / "docker/camofox/package.json").read_text())
    assert package["dependencies"] == {"@askjo/camofox-browser": "1.18.1"}
    lock = json.loads((ROOT / "docker/camofox/package-lock.json").read_text())
    assert lock["packages"]["node_modules/@askjo/camofox-browser"]["version"] == "1.18.1"
    block = _block()
    assert "COPY docker/camofox/package.json docker/camofox/package-lock.json /opt/camofox/" in block
    assert "npm ci --omit=dev" in block
    root = json.loads((ROOT / "package.json").read_text())
    every = {**root.get("dependencies", {}), **root.get("optionalDependencies", {}), **root.get("devDependencies", {})}
    assert "@askjo/camofox-browser" not in every


def test_camoufox_archive_is_pinned_and_verified() -> None:
    block = _block()
    assert re.search(r"ARG CAMOUFOX_RELEASE=152\.0\.4-beta\.30\n", block)
    assert re.search(r"ARG CAMOUFOX_X86_64_SHA256=[0-9a-f]{64}\n", block)
    assert "sha256sum -c -" in block
    assert "aarch64" not in block and 'uname -m' in block  # только amd64, остальное — понятная ошибка


def test_nothing_is_downloaded_at_runtime() -> None:
    block = _block()
    assert "CAMOFOX_SKIP_DOWNLOAD=1" in block
    assert re.search(r"ARG UBLOCK_XPI_SHA256=[0-9a-f]{64}\n", block)
    assert "addons/UBO" in block


def test_foreign_fonts_are_stripped_by_one_obvious_revertable_line() -> None:
    block = _block()
    assert "ARG CAMOFOX_STRIP_FOREIGN_FONTS=1" in block
    lines = [ln for ln in block.splitlines() if "fonts/macos" in ln and "rm -rf" in ln]
    assert len(lines) == 1
    assert 'CAMOFOX_STRIP_FOREIGN_FONTS}" = "1"' in lines[0]
    # в том же RUN, что распаковка: отдельным слоем размер образа не уменьшился бы
    run = block[block.index("zip=\"camoufox-"):]
    assert run.index("unzip -q \"/tmp/${zip}\"") < run.index("fonts/macos")


def test_firefox_system_libraries_are_installed() -> None:
    block = _block()
    for pkg in ("xvfb", "libgtk-3-0t64", "libasound2t64", "libdbus-glib-1-2", "libxt6t64"):
        assert pkg in block


def test_s6_service_is_declared_and_user_bundle_contains_it() -> None:
    assert (S6 / "camofox" / "type").read_text().strip() == "longrun"
    assert (S6 / "camofox" / "dependencies.d" / "base").exists()
    assert (S6 / "user" / "contents.d" / "camofox").exists()
    assert (S6 / "camofox" / "run").stat().st_mode & 0o111


def test_s6_run_starts_only_when_enabled_as_hermes_with_home_in_data() -> None:
    run = (S6 / "camofox" / "run").read_text()
    assert run.startswith("#!/command/with-contenv sh")
    assert "camofox/enabled" in run
    assert re.search(r"while \[ ! -f \"\$marker\" \]", run)
    assert "export HOME=/opt/data" in run
    assert "s6-setuidgid hermes python -m korra_cli.camofox_embedded serve" in run
    # 125 из finish делает слот навсегда «упавшим»: включить обратно нельзя
    assert not (S6 / "camofox" / "finish").exists()


def test_s6_run_never_exposes_the_key_or_a_public_bind() -> None:
    run = (S6 / "camofox" / "run").read_text()
    assert "ACCESS_KEY" not in run and "0.0.0.0" not in run
    assert "set -x" not in run and "xtrace" not in run


def test_marker_path_in_run_matches_the_python_state_dir() -> None:
    from korra_cli import camofox_embedded as emb

    assert emb.STATE_DIR_NAME == "camofox"
    assert emb._enabled_marker().name == "enabled"
    run = (S6 / "camofox" / "run").read_text()
    assert f'marker="$data_root/{emb.STATE_DIR_NAME}/{emb._enabled_marker().name}"' in run
