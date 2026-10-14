"""K21-336: образ содержит закреплённый Camofox, а s6-сервис работает всегда, пока его не выключили выключателем установки."""
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


def test_image_node_supports_synchronous_module_hooks() -> None:
    text = DOCKERFILE.read_text()
    source = re.search(
        r"^FROM node:(\d+)(?:\.(\d+)(?:\.(\d+))?)?[^\s]* AS node_source$",
        text, re.MULTILINE,
    )
    assert source, "образ должен закреплять версию Node для node_source"
    version = tuple(int(part or 0) for part in source.groups())
    # https://nodejs.org/api/module.html#moduleregisterhooksoptions
    # Added in 22.15.0 / 23.5.0; a major-only 22 or 23 tag gives no floor.
    assert (version[0] == 22 and version >= (22, 15, 0)) or version >= (23, 5, 0)
    assert re.search(r"^COPY\s+[^\n]*--from=node_source\s+/usr/local/bin/node\s", text, re.MULTILINE)


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
    # playwright-core 1.64 не восстанавливает сохранённый вход в Camoufox 152.0.4-beta.30
    # (Network.setRequestInterception: bypassServiceWorker); 1.63 работает.
    assert package["overrides"] == {"playwright-core": "1.63.0"}
    assert lock["packages"]["node_modules/playwright-core"]["version"] == "1.63.0"
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
    assert "sha256sum -c /tmp/camoufox.sha256" in block
    assert "| sha256sum" not in block  # hadolint DL4006: без pipe в RUN
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


def test_s6_run_starts_unless_switched_off_as_hermes_with_home_in_data() -> None:
    run = (S6 / "camofox" / "run").read_text()
    assert run.startswith("#!/command/with-contenv sh")
    assert 'switch="$data_root/camofox/disabled"' in run
    assert re.search(r"while \[ -f \"\$switch\" \]", run)  # ждёт, пока лежит выключатель
    assert "export HOME=/opt/data" in run
    assert "s6-setuidgid hermes python -m korra_cli.camofox_embedded serve" in run
    # 125 из finish делает слот навсегда «упавшим»: включить обратно нельзя
    assert not (S6 / "camofox" / "finish").exists()


def test_s6_run_never_exposes_the_key_or_a_public_bind() -> None:
    run = (S6 / "camofox" / "run").read_text()
    assert "ACCESS_KEY" not in run and "0.0.0.0" not in run
    assert "set -x" not in run and "xtrace" not in run


def test_switch_path_in_run_matches_the_python_state_dir() -> None:
    from korra_cli import camofox_embedded as emb

    assert emb.STATE_DIR_NAME == "camofox"
    assert emb._disabled_marker().name == "disabled"
    run = (S6 / "camofox" / "run").read_text()
    assert f'switch="$data_root/{emb.STATE_DIR_NAME}/{emb._disabled_marker().name}"' in run


def test_unix_socket_wrapper_is_copied_after_the_heavy_layer_and_inside_the_block() -> None:
    block = _block()
    copy = "COPY docker/camofox/unix-socket.mjs /opt/camofox/unix-socket.mjs"
    assert copy in block
    assert block.index(copy) > block.index("test -x /opt/camofox/browser/camoufox-bin")
    assert (ROOT / "docker" / "camofox" / "unix-socket.mjs").is_file()


def test_s6_run_has_no_tcp_port_or_bind_host() -> None:
    run = (S6 / "camofox" / "run").read_text()
    assert "PORT" not in run and "9377" not in run
    assert "s6-svc -u" in run  # подсказка для службы, остановленной через -d


def test_stage2_hands_the_camofox_state_dir_to_hermes_in_the_targeted_chown() -> None:
    hook = (ROOT / "docker" / "stage2-hook.sh").read_text()
    loop = next(ln for ln in hook.splitlines() if ln.strip().startswith("for sub in cron sessions"))
    assert re.search(r"\bcamofox\b", loop)
    assert "chown_hermes_tree" in hook


def test_stage2_chown_loop_stays_targeted() -> None:
    hook = (ROOT / "docker" / "stage2-hook.sh").read_text()
    assert "chown -R hermes:hermes \"$HERMES_HOME\"\n" not in hook


def test_s6_run_has_no_profile_accounting_and_does_not_wait_for_a_marker_of_use() -> None:
    run = (S6 / "camofox" / "run").read_text()
    assert "camofox_embedded sync" not in run and "enabled" not in run
    assert "s6-svc -t" in run  # подсказка: выключение останавливает слот, он вернётся в ожидание


def test_s6_run_executes_cleanly_when_the_server_is_missing_or_switched_off(tmp_path) -> None:
    import subprocess

    run = (S6 / "camofox" / "run").read_text().replace("#!/command/with-contenv sh", "#!/bin/sh", 1)
    script = tmp_path / "run"
    script.write_text(run.replace("sleep 3", "exit 7"))
    script.chmod(0o755)
    data = tmp_path / "data"
    (data / "camofox").mkdir(parents=True)
    (data / "camofox" / "disabled").write_text("1\n")
    env = {"PATH": "/usr/bin:/bin", "KORRA_HOME": str(data), "KORRA_CAMOFOX_HOME": str(tmp_path / "none")}
    assert subprocess.run(["sh", str(script)], env=env, timeout=10).returncode == 7  # в цикле ожидания


def test_wrapper_skips_the_browser_prewarm_without_touching_the_package() -> None:
    wrapper = (ROOT / "docker" / "camofox" / "unix-socket.mjs").read_text()
    assert "registerHooks" in wrapper and "browser pre-warmed" in wrapper
    assert "sed -i" not in _block() and "patch " not in _block()
