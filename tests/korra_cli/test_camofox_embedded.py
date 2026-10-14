"""K21-336: встроенный Camofox — Unix-сокет, ключ, изоляция установок и состояние."""

import os
import shutil
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


def test_server_belongs_to_the_installation_and_is_on_by_default(install, data):
    assert emb.is_enabled()
    assert not emb.is_disabled()
    assert emb.endpoint() == emb.BASE_URL
    assert not (data / "camofox").exists()  # включать ничего не нужно, файлов не требуется


def test_choosing_chromium_everywhere_does_not_switch_the_server_off(chromium_root, install, data):
    assert emb.is_enabled()
    assert emb.status()["state"] == "not_responding"  # сервис запущен s6, пока нет ответа


def test_endpoint_is_empty_without_the_image(tmp_path, monkeypatch, data):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "nothing"))
    assert emb.endpoint() == ""
    assert emb.status()["state"] == "not_installed"


def test_port_mechanism_is_gone(install, data):
    for name in ("PORT_RANGE", "BIND_HOST", "saved_port", "resolve_port_for_serve", "_pick_free_port", "_port_file"):
        assert not hasattr(emb, name), name
    emb.ensure_state()
    assert not (data / "camofox" / "port").exists()
    assert "CAMOFOX_PORT" not in emb.server_env()


def test_ensure_state_creates_private_state_and_key(install, data):
    emb.ensure_state()
    assert len(emb.access_key()) >= 32
    assert stat.S_IMODE((data / "camofox" / "access.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((data / "camofox").stat().st_mode) == 0o700
    assert emb.socket_path() == data / "camofox" / "server.sock"


def test_key_is_stable_across_calls(install, data):
    emb.ensure_state()
    key = emb.access_key()
    emb.enable()
    emb.ensure_state()
    assert emb.access_key() == key


def test_two_installs_get_different_keys_and_sockets(install, data, monkeypatch):
    emb.ensure_state()
    key_a, sock_a = emb.access_key(), emb.socket_path()
    other = use_root(monkeypatch, short_dir())
    try:
        emb.ensure_state()
        assert emb.access_key() != key_a
        assert emb.socket_path() != sock_a
        assert (other / "camofox" / "access.key").read_text().strip() == emb.access_key()
        assert (data / "camofox" / "access.key").read_text().strip() == key_a
    finally:
        import shutil

        shutil.rmtree(other, ignore_errors=True)


def test_concurrent_switching_never_fails_and_keeps_the_key(install, data):
    # Воспроизведение race.py из ревью: общий «.tmp» ронял параллельные записи.
    emb.ensure_state()
    key = emb.access_key()
    barrier = threading.Barrier(2)

    def call(index):
        barrier.wait()
        try:
            emb.disable(signal=False) if index == 0 else emb.enable()
            return "ok"
        except Exception as exc:  # pragma: no cover — именно это и ловим
            return type(exc).__name__

    failures = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(30):
            failures.extend(x for x in pool.map(call, range(2)) if x != "ok")
    assert failures == []
    assert emb.access_key() == key
    assert not [p for p in (data / "camofox").iterdir() if p.suffix == ".tmp"]


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
    emb.ensure_state()
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
    emb.ensure_state()
    assert "BROWSER_IDLE_TIMEOUT_MS" not in emb.server_env()
    monkeypatch.setenv("KORRA_CAMOFOX_IDLE_TIMEOUT_MS", "15000")
    assert emb.server_env()["BROWSER_IDLE_TIMEOUT_MS"] == "15000"
    monkeypatch.setenv("KORRA_CAMOFOX_IDLE_TIMEOUT_MS", "abc")
    assert "BROWSER_IDLE_TIMEOUT_MS" not in emb.server_env()


def test_the_switch_is_one_named_file_and_keeps_key_and_profiles(install, data):
    emb.ensure_state()
    (data / "camofox" / "profiles").mkdir()
    key = emb.access_key()
    emb.disable()
    assert emb.is_disabled() and not emb.is_enabled()
    assert (data / "camofox" / "disabled").is_file()
    assert emb.endpoint() == emb.BASE_URL  # идентичность не зависит от того, запущен ли сервер
    assert emb.access_key() == key
    assert (data / "camofox" / "profiles").is_dir()
    emb.enable()
    assert emb.is_enabled() and not (data / "camofox" / "disabled").exists()
    emb.enable()  # повторное включение безвредно
    assert emb.access_key() == key


def test_switching_off_works_on_a_clean_data_dir(install, data):
    emb.disable()
    assert emb.status()["state"] == "switched_off"
    assert emb.status()["next"] == "включить: korra tools camofox on"


def test_s6_is_nudged_only_inside_the_container_and_only_on_switch_off(install, data, monkeypatch):
    calls = []
    monkeypatch.setattr(emb.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    emb.disable()
    emb.enable()
    assert calls == []
    monkeypatch.setattr(emb, "in_container", lambda: True)
    monkeypatch.setattr(emb.shutil, "which", lambda name: "/usr/bin/s6-svc")
    emb.enable()
    assert calls == []  # включение — снять файл: run-скрипт сам заметит это за 3 с
    emb.disable()
    assert calls == [["/usr/bin/s6-svc", "-t", "/run/service/camofox"]]


def test_switch_off_finds_s6_svc_outside_the_service_path(install, data, monkeypatch):
    calls = []
    monkeypatch.setattr(emb.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(emb, "in_container", lambda: True)
    monkeypatch.setattr(emb.shutil, "which", lambda name: None)
    emb.disable()
    assert calls == [["/command/s6-svc", "-t", "/run/service/camofox"]]


def test_the_switch_command_reports_every_state_in_russian(install, data, capsys):
    assert emb.main(["status"]) == 1  # включён, но процесса нет
    assert emb.tools_command("off") == 0
    out = capsys.readouterr().out
    assert "выключен" in out and "korra tools camofox on" in out and "Chromium" in out
    assert emb.is_disabled()
    assert emb.tools_command("status") == 0
    assert emb.tools_command("on") == 0
    assert emb.is_enabled()
    emb.ensure_state()
    assert emb.access_key() not in capsys.readouterr().out


def test_the_switch_command_does_nothing_outside_the_image(tmp_path, monkeypatch, data, capsys):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "nothing"))
    assert emb.tools_command("off") == 1
    assert not (data / "camofox").exists()
    assert "нет встроенного Camofox" in capsys.readouterr().out


# ── Транспорт ───────────────────────────────────────────────────────────────


def test_health_over_the_socket(install, data):
    emb.ensure_state()
    assert emb.health(timeout=0.5) is False
    with UnixServer(emb.socket_path()) as server:
        assert emb.health(timeout=2) is True
    assert server.seen[0]["path"] == "/health"


def test_health_ignores_a_tcp_listener_squatting_on_loopback(install, data):
    # Подмена адреса из ревью: чужой процесс на loopback-порту не может быть
    # принят за сервер установки и не получает ничего.
    emb.ensure_state()
    with TcpSpy() as spy:
        assert emb.health(timeout=0.5) is False
        assert emb.status()["state"] != "running"
    assert spy.seen == []


def test_unix_session_sends_the_key_only_to_the_socket(install, data):
    emb.ensure_state()
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
    emb.ensure_state()
    with UnixServer(emb.socket_path()):
        assert emb.unix_session().get(f"{emb.BASE_URL}/health", timeout=3).status_code == 200


def test_too_long_socket_path_is_reported_and_never_served(install, tmp_path, monkeypatch):
    deep = tmp_path.joinpath(*["d" * 20] * 6)
    use_root(monkeypatch, deep)
    assert len(os.fsencode(str(emb.socket_path()))) > emb.MAX_SOCKET_PATH
    assert "путь к сокету" in emb.socket_path_problem()
    emb.ensure_state()
    assert emb.health() is False
    info = emb.status()
    assert info["state"] == "not_responding"
    assert "путь к сокету" in info["next"]
    with pytest.raises(SystemExit):
        emb.serve()


# ── Состояния ───────────────────────────────────────────────────────────────


def test_status_running_and_not_responding(install, data):
    emb.ensure_state()
    with UnixServer(emb.socket_path()):
        info = emb.status()
        assert info["state"] == "running"
    info = emb.status()
    assert info["state"] == "not_responding"
    assert "s6-svc -u /run/service/camofox" in info["next"]
    assert "s6-svc -t /run/service/camofox" in info["next"]
    assert emb.access_key() not in str(info)


def test_serve_execs_node_through_the_launcher_with_clean_environment(install, data, monkeypatch):
    emb.ensure_state()
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
    emb.ensure_state()
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
    emb.ensure_state()
    assert emb.main(["status"]) == 1
    out = capsys.readouterr()
    assert emb.access_key() not in out.out + out.err


def test_files_made_by_root_are_handed_to_the_data_owner(install, data, monkeypatch):
    owner = data.stat()
    chowned = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: chowned.append((Path(path).name, uid, gid)))
    emb.disable(signal=False)
    names = {name for name, _, _ in chowned}
    assert "camofox" in names
    # Временные файлы с уникальными именами тоже передаются владельцу до публикации.
    assert any(n.startswith("disabled.") and n.endswith(".tmp") for n in names)
    assert all((uid, gid) == (owner.st_uid, owner.st_gid) for _, uid, gid in chowned)


# ── Умолчание: профиль без выбора браузера пользуется встроенным Camofox ───


def _save(root, name, browser):
    from korra_cli.config import save_config

    with in_profile(root, name):
        save_config({"browser": browser})


def _uses_default(root, name):
    with in_profile(root, name):
        return emb.uses_default_browser()


def test_a_profile_without_a_choice_uses_the_default_and_explicit_choices_are_kept(install, data):
    _save(data, "plain", {})
    _save(data, "chromium", {"cloud_provider": "local"})
    _save(data, "lightpanda", {"cloud_provider": "local", "engine": "lightpanda"})
    _save(data, "cloud", {"cloud_provider": "browserbase"})
    _save(data, "cdp", {"cdp_url": "http://127.0.0.1:9222"})
    _save(data, "driver", {"backend": "browser-use"})
    _save(data, "real", {"use_real_profile": True})
    names = ["plain", "chromium", "lightpanda", "cloud", "cdp", "driver", "real"]
    assert {n: _uses_default(data, n) for n in names} == {
        "plain": True,
        "chromium": False,
        "lightpanda": False,
        "cloud": False,
        "cdp": False,
        "driver": False,
        "real": False,
    }


def test_cloud_credentials_and_cdp_in_the_environment_keep_the_old_browser(install, data, monkeypatch):
    assert _uses_default(data, None) is True
    monkeypatch.setenv("BROWSERBASE_API_KEY", "k")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "p")
    assert _uses_default(data, None) is False
    monkeypatch.delenv("BROWSERBASE_API_KEY")
    monkeypatch.delenv("BROWSERBASE_PROJECT_ID")
    monkeypatch.setenv("BROWSER_CDP_URL", "ws://127.0.0.1:9222/devtools/browser/x")
    assert _uses_default(data, None) is False


def test_unreadable_config_is_not_treated_as_no_choice(install, data):
    (data / "config.yaml").write_text("browser: [unclosed\n")
    assert _uses_default(data, None) is False


def test_external_camofox_url_is_read_from_the_current_profile_only(install, data, monkeypatch):
    assert emb.explicit_url() == ""
    monkeypatch.setenv("CAMOFOX_URL", "http://external.example:9377")
    assert emb.explicit_url() == "http://external.example:9377"


def test_nothing_changes_outside_the_image(tmp_path, monkeypatch, data):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    assert emb.uses_default_browser() is False
    assert emb.is_enabled() is False
    assert not (data / "camofox").exists()


# ── Учёта потребителей больше нет ───────────────────────────────────────────


def test_consumer_accounting_is_gone(install, data):
    for name in ("consumers", "sync", "wants_embedded", "current_explicit_url", "_profile_homes", "_InHome", "_enabled_marker"):
        assert not hasattr(emb, name), name
    assert emb.main(["sync"]) == 2


def test_cloud_key_of_one_profile_cannot_switch_the_server_for_another(install, data, monkeypatch):
    # Находка 1 ревью: ключ облака профиля A лишал профиль B встроенного
    # браузера и останавливал сервер. Выбор профиля A больше не трогает сервер,
    # а профиль B читает только свои настройки.
    _save(data, "a", {"cloud_provider": "local"})
    _save(data, "fresh", {})
    (data / "profiles" / "a" / ".env").write_text("BROWSER_USE_API_KEY=fake-key-of-a\n")
    emb.ensure_state()
    with in_profile(data, "a"):
        choose_browser("Local Browser")
    assert emb.is_enabled()
    assert _uses_default(data, "fresh") is True


def test_choosing_browsers_never_changes_the_server_switch(install, data, monkeypatch):
    calls = []
    monkeypatch.setattr(emb, "_s6_svc", lambda flag: calls.append(flag))
    monkeypatch.setattr(emb, "in_container", lambda: True)
    for profile in ("a", "b", None):
        with in_profile(data, profile):
            choose_browser("Local Browser")
            choose_browser("Camofox")
            choose_browser("Local Browser")
    assert emb.is_enabled() and calls == []
    assert not (data / "camofox").exists()


def test_owner_switch_survives_any_browser_choice(install, data):
    emb.disable(signal=False)
    with in_profile(data, "a"):
        choose_browser("Camofox")
    assert emb.is_disabled()
    emb.enable()
    with in_profile(data, "a"):
        choose_browser("Local Browser")
    assert emb.is_enabled()


def test_korra_tools_camofox_command_reaches_the_switch(install, data):
    import subprocess
    import sys

    env = {**os.environ, "HOME": str(data), "KORRA_HOME": str(data), "HERMES_HOME": str(data), "KORRA_CAMOFOX_HOME": str(install)}

    def tools(action):
        return subprocess.run(
            [sys.executable, "-m", "korra_cli.main", "tools", "camofox", action],
            env=env, capture_output=True, text=True, timeout=60,
        )

    assert tools("off").returncode == 0
    assert (data / "camofox" / "disabled").is_file()
    status = tools("status")
    assert "выключен" in status.stdout
    assert tools("on").returncode == 0
    assert not (data / "camofox" / "disabled").exists()


# ── Удаление профиля убирает его вход во встроенном Camofox ────────────────


def _saved_login(data, profile):
    """Как клиент и сервер: userId из профиля, каталог сервера — sha256 от него."""
    import hashlib

    from tools.browser_camofox_state import get_camofox_identity

    with in_profile(data, profile):
        user_id = get_camofox_identity("t")["user_id"]
    folder = data / "camofox" / "profiles" / hashlib.sha256(user_id.encode()).hexdigest()[:32]
    folder.mkdir(parents=True)
    (folder / "storage-state.json").write_text('{"cookies": [{"name": "sid"}]}')
    return user_id, folder


def _delete(name):
    from korra_cli import profiles

    profiles.delete_profile(name, yes=True)


def test_deleting_a_profile_removes_its_saved_login_and_only_its_own(install, data):
    (data / "profiles" / "gone").mkdir(parents=True)
    (data / "profiles" / "stays").mkdir(parents=True)
    _, gone = _saved_login(data, "gone")
    _, stays = _saved_login(data, "stays")
    _delete("gone")
    assert not gone.exists()
    assert (stays / "storage-state.json").is_file()
    assert (data / "camofox" / "profiles").is_dir()


def test_the_running_server_is_told_to_close_the_session_before_the_files_go(install, data):
    (data / "profiles" / "gone").mkdir(parents=True)
    user_id, folder = _saved_login(data, "gone")
    emb.ensure_state()
    present_at_request = []

    def respond(method, path, body):
        present_at_request.append(folder.exists())

    with UnixServer(emb.socket_path(), respond=respond) as server:
        _delete("gone")
    deletes = [r for r in server.seen if r["method"] == "DELETE"]
    assert [r["path"] for r in deletes] == [f"/sessions/{user_id}"]
    assert deletes[0]["auth"] == f"Bearer {emb.access_key()}"
    assert present_at_request == [True]
    assert not folder.exists()


def test_a_live_session_without_saved_files_is_closed_and_leaves_nothing_behind(install, data):
    # Вход ещё не сохранён на диск: сервер запишет его при закрытии сессии (так было в живой проверке).
    (data / "profiles" / "gone").mkdir(parents=True)
    _, folder = _saved_login(data, "gone")
    shutil.rmtree(folder)
    emb.ensure_state()

    def respond(method, path, body):
        folder.mkdir(parents=True)
        (folder / "storage-state.json").write_text('{"cookies": [{"name": "sid"}]}')

    with UnixServer(emb.socket_path(), respond=respond) as server:
        _delete("gone")
    assert [r["method"] for r in server.seen] == ["DELETE"]
    assert not folder.exists()


def test_a_dead_server_does_not_stop_the_deletion(install, data):
    (data / "profiles" / "gone").mkdir(parents=True)
    _, folder = _saved_login(data, "gone")
    emb.ensure_state()
    _delete("gone")
    assert not folder.exists()
    assert not (data / "profiles" / "gone").exists()


def test_an_external_server_and_a_shared_identity_are_left_alone(install, data, monkeypatch):
    (data / "profiles" / "ext").mkdir(parents=True)
    (data / "profiles" / "ext" / ".env").write_text('CAMOFOX_URL="http://camofox.example:9377"\n')
    (data / "profiles" / "shared").mkdir(parents=True)
    (data / "profiles" / "shared" / "config.yaml").write_text("browser:\n  camofox:\n    user_id: team-browser\n")
    _, ext = _saved_login(data, "ext")
    _, shared = _saved_login(data, "shared")
    _delete("ext")
    _delete("shared")
    assert ext.is_dir() and shared.is_dir()
    (data / "profiles" / "late").mkdir(parents=True)
    _, late = _saved_login(data, "late")
    monkeypatch.setenv("CAMOFOX_URL", "http://camofox.example:9377")
    _delete("late")
    assert late.is_dir()


def test_deleting_a_profile_without_a_saved_login_or_without_the_server_works(tmp_path, monkeypatch, data):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    (data / "profiles" / "plain").mkdir(parents=True)
    _delete("plain")
    assert not (data / "profiles" / "plain").exists()
    assert not (data / "camofox").exists()


def test_the_login_folder_is_removed_even_when_the_switch_is_off(install, data):
    (data / "profiles" / "gone").mkdir(parents=True)
    _, folder = _saved_login(data, "gone")
    emb.disable(signal=False)
    _delete("gone")
    assert not folder.exists()
    assert emb.is_disabled()
