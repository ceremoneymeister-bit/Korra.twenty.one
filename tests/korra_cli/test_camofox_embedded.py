"""K21-336: встроенный Camofox — Unix-сокет, ключ, изоляция установок и состояние."""

import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from korra_cli import camofox_embedded as emb
from tests.fakes.camofox_embedded import (  # noqa: F401  (фикстуры)
    TcpSpy,
    UnixServer,
    choose_browser,
    chromium_root,
    data,
    in_profile,
    install,
    short_dir,
    use_root,
)


def test_endpoint_is_the_unix_base_url_and_nothing_runs_before_the_first_sync(install, data):
    assert not emb.is_enabled()
    assert emb.endpoint() == emb.BASE_URL
    # Профиль не выбирал браузер: он потребитель по умолчанию, ждёт синхронизации.
    assert emb.status()["state"] == "selected_off"
    assert emb.sync() is True
    assert emb.is_enabled()


def test_server_stays_off_when_every_profile_chose_chromium(chromium_root, install, data):
    assert not emb.is_enabled()
    assert emb.status()["state"] == "disabled"
    assert emb.sync() is False


def test_endpoint_is_empty_without_the_image(tmp_path, monkeypatch, data):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "nothing"))
    assert emb.endpoint() == ""
    assert emb.status()["state"] == "not_installed"


def test_port_mechanism_is_gone(install, data):
    for name in ("PORT_RANGE", "BIND_HOST", "saved_port", "resolve_port_for_serve", "_pick_free_port", "_port_file"):
        assert not hasattr(emb, name), name
    emb.enable()
    assert not (data / "camofox" / "port").exists()
    assert "CAMOFOX_PORT" not in emb.server_env()


def test_enable_creates_private_state_and_key(install, data):
    emb.enable()
    assert emb.is_enabled()
    assert len(emb.access_key()) >= 32
    assert stat.S_IMODE((data / "camofox" / "access.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((data / "camofox").stat().st_mode) == 0o700
    assert emb.socket_path() == data / "camofox" / "server.sock"


def test_key_is_stable_across_calls(install, data):
    emb.enable()
    key = emb.access_key()
    emb.enable()
    emb.ensure_state()
    assert emb.access_key() == key


def test_two_installs_get_different_keys_and_sockets(install, data, monkeypatch):
    emb.enable()
    key_a, sock_a = emb.access_key(), emb.socket_path()
    other = use_root(monkeypatch, short_dir())
    try:
        assert not emb.is_enabled()
        emb.enable()
        assert emb.access_key() != key_a
        assert emb.socket_path() != sock_a
        assert (other / "camofox" / "access.key").read_text().strip() == emb.access_key()
        assert (data / "camofox" / "access.key").read_text().strip() == key_a
    finally:
        import shutil

        shutil.rmtree(other, ignore_errors=True)


def test_concurrent_enable_never_fails_and_keeps_the_key(install, data):
    # Воспроизведение race.py из ревью: общий «.tmp» ронял параллельные записи.
    emb.enable()
    key = emb.access_key()
    barrier = threading.Barrier(2)

    def call(_):
        barrier.wait()
        try:
            emb.enable()
            return "ok"
        except Exception as exc:  # pragma: no cover — именно это и ловим
            return type(exc).__name__

    failures = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(30):
            failures.extend(x for x in pool.map(call, range(2)) if x != "ok")
    assert failures == []
    assert emb.access_key() == key
    assert sorted(p.name for p in (data / "camofox").iterdir()) == ["access.key", "enabled"]


def test_first_key_is_created_atomically_and_seen_identically_by_everyone(install, data):
    # Одновременные включение и старт сервиса: все видят один ключ, читатель
    # не застаёт неполный файл.
    workers = 12
    barrier = threading.Barrier(workers + 1)
    seen = set()
    stop = threading.Event()

    def reader():
        barrier.wait()
        while not stop.is_set():
            key = emb.access_key()
            if key:
                seen.add(key)

    def writer(_):
        barrier.wait()
        emb.ensure_state()
        return emb.access_key()

    thread = threading.Thread(target=reader)
    thread.start()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        keys = list(pool.map(writer, range(workers)))
    stop.set()
    thread.join()

    assert len(set(keys)) == 1 and keys[0]
    assert seen <= set(keys)
    assert len(keys[0]) >= 32
    assert not [p for p in (data / "camofox").iterdir() if p.suffix == ".tmp"]


def test_server_env_has_socket_no_telemetry_and_no_foreign_secrets(install, data, monkeypatch):
    emb.enable()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("CAMOFOX_API_KEY", "external-key")
    monkeypatch.setenv("SENTRY_DSN", "https://x@y/1")
    env = emb.server_env()

    assert env["CAMOFOX_UNIX_SOCKET"] == str(data / "camofox" / "server.sock")
    assert env["CAMOFOX_BIND_HOST"] == "127.0.0.1"
    assert env["CAMOFOX_CRASH_REPORT_ENABLED"] == "false"
    assert env["CAMOFOX_ACCESS_KEY"] == emb.access_key()
    assert env["CAMOUFOX_INSTALL_DIR"] == str(install / "browser")
    assert env["CAMOFOX_PROFILE_DIR"] == str(data / "camofox" / "profiles")
    joined = "\n".join(env.values())
    for secret in ("tg-secret", "sk-secret", "external-key", "x@y"):
        assert secret not in joined
    assert "PROXY_HOST" not in env and "ENABLE_VNC" not in env


def test_idle_timeout_override_is_passed_only_when_numeric(install, data, monkeypatch):
    emb.enable()
    assert "BROWSER_IDLE_TIMEOUT_MS" not in emb.server_env()
    monkeypatch.setenv("KORRA_CAMOFOX_IDLE_TIMEOUT_MS", "15000")
    assert emb.server_env()["BROWSER_IDLE_TIMEOUT_MS"] == "15000"
    monkeypatch.setenv("KORRA_CAMOFOX_IDLE_TIMEOUT_MS", "abc")
    assert "BROWSER_IDLE_TIMEOUT_MS" not in emb.server_env()


def test_disable_removes_marker_but_keeps_key_and_profiles(install, data):
    emb.enable()
    (data / "camofox" / "profiles").mkdir()
    key = emb.access_key()
    emb.disable()
    assert not emb.is_enabled()
    assert emb.endpoint() == emb.BASE_URL  # идентичность не зависит от того, запущен ли сервер
    assert emb.access_key() == key
    assert (data / "camofox" / "profiles").is_dir()


def test_s6_is_nudged_only_inside_the_container(install, data, monkeypatch):
    calls = []
    monkeypatch.setattr(emb.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    emb.enable()
    emb.disable()
    assert calls == []
    monkeypatch.setattr(emb, "in_container", lambda: True)
    emb.enable()
    assert calls == []  # включение — только маркер: run-скрипт сам заметит его за 3 с
    emb.disable()
    assert calls == [["s6-svc", "-t", "/run/service/camofox"]]


# ── Транспорт ───────────────────────────────────────────────────────────────


def test_health_over_the_socket(install, data):
    emb.enable()
    assert emb.health(timeout=0.5) is False
    with UnixServer(emb.socket_path()) as server:
        assert emb.health(timeout=2) is True
    assert server.seen[0]["path"] == "/health"


def test_health_ignores_a_tcp_listener_squatting_on_loopback(install, data):
    # Подмена адреса из ревью: чужой процесс на loopback-порту не может быть
    # принят за сервер установки и не получает ничего.
    emb.enable()
    with TcpSpy() as spy:
        assert emb.health(timeout=0.5) is False
        assert emb.status()["state"] != "running"
    assert spy.seen == []


def test_unix_session_sends_the_key_only_to_the_socket(install, data):
    emb.enable()
    with UnixServer(emb.socket_path()) as server, TcpSpy() as spy:
        session = emb.unix_session()
        resp = session.get(
            f"{emb.BASE_URL}/tabs", headers={"Authorization": f"Bearer {emb.access_key()}"}, timeout=3
        )
        assert resp.status_code == 200
        # Любой другой адрес, в том числе редирект наружу, не отправляется.
        import requests

        with pytest.raises(requests.ConnectionError):
            session.get(f"http://127.0.0.1:{spy.port}/tabs", headers={"Authorization": "Bearer k"}, timeout=3)
    assert server.seen[0]["auth"] == f"Bearer {emb.access_key()}"
    assert server.seen[0]["host"] == emb.UNIX_HOST
    assert spy.seen == []


def test_unix_session_ignores_proxy_environment(install, data, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    emb.enable()
    with UnixServer(emb.socket_path()):
        assert emb.unix_session().get(f"{emb.BASE_URL}/health", timeout=3).status_code == 200


def test_too_long_socket_path_is_reported_and_never_served(install, tmp_path, monkeypatch):
    deep = tmp_path.joinpath(*["d" * 20] * 6)
    use_root(monkeypatch, deep)
    assert len(os.fsencode(str(emb.socket_path()))) > emb.MAX_SOCKET_PATH
    assert "путь к сокету" in emb.socket_path_problem()
    emb.enable()
    assert emb.health() is False
    info = emb.status()
    assert info["state"] == "not_responding"
    assert "путь к сокету" in info["next"]
    with pytest.raises(SystemExit):
        emb.serve()


# ── Состояния ───────────────────────────────────────────────────────────────


def test_status_running_and_not_responding(install, data, monkeypatch):
    with in_profile(data, None):
        choose_browser("Camofox")
    assert emb.is_enabled()
    with UnixServer(emb.socket_path()):
        info = emb.status()
        assert info["state"] == "running"
        assert info["consumers"] == ["default"]
    info = emb.status()
    assert info["state"] == "not_responding"
    assert "s6-svc -u /run/service/camofox" in info["next"]
    assert "s6-svc -t /run/service/camofox" in info["next"]
    assert emb.access_key() not in str(info)


def test_status_selected_off_unused_and_disabled(install, data):
    with in_profile(data, None):
        choose_browser("Camofox")
    emb._enabled_marker().unlink()  # маркер рассинхронизировался с настройками
    info = emb.status()
    assert info["state"] == "selected_off"
    assert "korra doctor --fix" in info["next"]

    assert emb.sync() is True
    assert emb.status()["state"] != "selected_off"

    with in_profile(data, None):
        choose_browser("Local Browser")
    assert not emb.is_enabled()
    assert emb.status()["state"] == "disabled"

    emb.enable()  # сервер включён вручную, потребителей нет
    assert emb.status()["state"] == "unused"
    assert emb.sync() is False
    assert emb.status()["state"] == "disabled"


def test_serve_execs_node_through_the_launcher_with_clean_environment(install, data, monkeypatch):
    emb.enable()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    calls = {}

    def fake_exec(file, args, env):
        calls.update(file=file, args=args, env=env)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvpe", fake_exec)
    monkeypatch.setattr(os, "chdir", lambda path: calls.setdefault("cwd", path))
    with pytest.raises(SystemExit):
        emb.serve()
    assert calls["file"] == "node"
    assert calls["args"][1] == str(install / "unix-socket.mjs")
    assert calls["args"][2].endswith("camofox-browser/server.js")
    assert "OPENAI_API_KEY" not in calls["env"]
    assert (data / "camofox" / "profiles").is_dir()


def test_serve_removes_a_stale_socket_before_start(install, data, monkeypatch):
    emb.enable()
    emb.socket_path().write_text("stale")
    monkeypatch.setattr(os, "execvpe", lambda *a: (_ for _ in ()).throw(SystemExit(0)))
    monkeypatch.setattr(os, "chdir", lambda path: None)
    with pytest.raises(SystemExit):
        emb.serve()
    assert not emb.socket_path().exists()


def test_serve_refuses_when_the_launcher_is_missing(install, data):
    (install / "unix-socket.mjs").unlink()
    with pytest.raises(SystemExit) as exc:
        emb.serve()
    assert exc.value.code == 1


def test_cli_status_never_prints_the_key(install, data, capsys):
    emb.enable()
    assert emb.main(["status"]) == 1
    out = capsys.readouterr()
    assert emb.access_key() not in out.out + out.err


def test_files_made_by_root_are_handed_to_the_data_owner(install, data, monkeypatch):
    owner = data.stat()
    chowned = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: chowned.append((Path(path).name, uid, gid)))
    emb.enable()
    names = {name for name, _, _ in chowned}
    assert "camofox" in names
    # Временные файлы с уникальными именами тоже передаются владельцу до публикации.
    assert any(n.startswith("access.key.") and n.endswith(".tmp") for n in names)
    assert any(n.startswith("enabled.") and n.endswith(".tmp") for n in names)
    assert all((uid, gid) == (owner.st_uid, owner.st_gid) for _, uid, gid in chowned)


# ── Умолчание: профиль без выбора браузера пользуется встроенным Camofox ───


def _save(root, name, browser):
    from korra_cli.config import save_config

    with in_profile(root, name):
        save_config({"browser": browser})


def test_profiles_without_a_choice_are_consumers_and_explicit_choices_are_kept(install, data):
    _save(data, "plain", {})
    _save(data, "chromium", {"cloud_provider": "local"})
    _save(data, "lightpanda", {"cloud_provider": "local", "engine": "lightpanda"})
    _save(data, "cloud", {"cloud_provider": "browserbase"})
    _save(data, "cdp", {"cdp_url": "http://127.0.0.1:9222"})
    _save(data, "driver", {"backend": "browser-use"})
    _save(data, "real", {"use_real_profile": True})
    assert sorted(emb.consumers()) == ["default", "plain"]
    assert emb.sync() is True


def test_cloud_credentials_and_cdp_in_the_environment_keep_the_old_browser(install, data, monkeypatch):
    assert emb.consumers() == ["default"]
    monkeypatch.setenv("BROWSERBASE_API_KEY", "k")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "p")
    assert emb.consumers() == []
    monkeypatch.delenv("BROWSERBASE_API_KEY")
    monkeypatch.delenv("BROWSERBASE_PROJECT_ID")
    monkeypatch.setenv("BROWSER_CDP_URL", "ws://127.0.0.1:9222/devtools/browser/x")
    assert emb.consumers() == []


def test_unreadable_config_is_not_treated_as_no_choice(install, data):
    (data / "config.yaml").write_text("browser: [unclosed\n")
    assert emb.consumers() == []


def test_external_camofox_url_beats_the_default(install, data, monkeypatch):
    monkeypatch.setenv("CAMOFOX_URL", "http://external.example:9377")
    assert emb.consumers() == []
    assert emb.sync() is False
    assert not emb.is_enabled()


def test_nothing_changes_outside_the_image(tmp_path, monkeypatch, data):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    assert emb.uses_default_browser() is False
    assert emb.sync() is False
    assert not (data / "camofox").exists()


def test_boot_sync_does_not_restart_the_service_that_runs_it(install, data, monkeypatch):
    calls = []
    monkeypatch.setattr(emb, "_s6_svc", lambda flag: calls.append(flag))
    emb.enable()
    _save(data, None, {"cloud_provider": "local"})
    assert emb.main(["sync"]) == 0
    assert not emb.is_enabled()
    assert calls == []  # через `sync` из run-скрипта s6 сигнал не посылается
    assert emb.sync() is False
    emb.enable()
    assert emb.sync() is False and calls == ["-t"]


def test_boot_sync_enables_a_fleet_that_never_chose_a_browser(install, data, capsys):
    assert not emb.is_enabled()
    assert emb.main(["sync"]) == 0
    assert capsys.readouterr().out.strip() == "нужен"
    assert emb.is_enabled()
