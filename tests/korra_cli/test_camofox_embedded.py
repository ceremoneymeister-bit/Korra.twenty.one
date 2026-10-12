"""K21-336: встроенный Camofox — адрес, ключ, изоляция двух установок и состояние."""

import os
import stat
from pathlib import Path

import pytest

from korra_cli import camofox_embedded as emb


@pytest.fixture
def install(tmp_path, monkeypatch):
    """Образ с Camofox: сервер и бинарник на месте, данные во временном каталоге."""
    home = tmp_path / "opt-camofox"
    server = home.joinpath(*emb._SERVER_RELPATH)
    server.parent.mkdir(parents=True)
    server.write_text("// stub")
    (home / "browser").mkdir()
    (home / "browser" / "camoufox-bin").write_text("")
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(home))
    return home


def use_data(monkeypatch, path: Path):
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(path))
    return path


def test_disabled_by_default_and_has_no_endpoint(install, tmp_path, monkeypatch):
    use_data(monkeypatch, tmp_path / "a")
    assert not emb.is_enabled()
    assert emb.endpoint() == ""
    assert emb.status()["state"] == "disabled"


def test_enable_creates_loopback_endpoint_and_private_key(install, tmp_path, monkeypatch):
    data = use_data(monkeypatch, tmp_path / "a")
    emb.enable()
    url = emb.endpoint()
    assert url.startswith("http://127.0.0.1:")
    port = int(url.rsplit(":", 1)[1])
    assert emb.PORT_RANGE[0] <= port <= emb.PORT_RANGE[1]
    key = emb.access_key()
    assert len(key) >= 32
    key_file = data / "camofox" / "access.key"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE((data / "camofox").stat().st_mode) == 0o700


def test_port_and_key_are_stable_across_calls(install, tmp_path, monkeypatch):
    use_data(monkeypatch, tmp_path / "a")
    emb.enable()
    first = (emb.endpoint(), emb.access_key())
    emb.enable()
    emb.ensure_state()
    assert (emb.endpoint(), emb.access_key()) == first


def test_two_installs_get_different_key_and_do_not_share_state(install, tmp_path, monkeypatch):
    use_data(monkeypatch, tmp_path / "a")
    emb.enable()
    key_a, port_a = emb.access_key(), emb.saved_port()

    use_data(monkeypatch, tmp_path / "b")
    assert not emb.is_enabled()
    emb.enable()
    key_b, port_b = emb.access_key(), emb.saved_port()

    assert key_a != key_b
    assert (tmp_path / "a" / "camofox" / "access.key").read_text().strip() == key_a
    assert (tmp_path / "b" / "camofox" / "access.key").read_text().strip() == key_b
    assert port_a and port_b


def test_pick_free_port_skips_a_port_already_taken_on_the_host(monkeypatch):
    import socket

    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    taken = busy.getsockname()[1]
    try:
        monkeypatch.setattr(emb, "PORT_RANGE", (taken, taken + 1))
        assert emb._pick_free_port() == taken + 1
    finally:
        busy.close()


def test_serve_picks_new_port_when_saved_one_is_taken(install, tmp_path, monkeypatch):
    import socket

    use_data(monkeypatch, tmp_path / "a")
    emb.enable()
    saved = emb.saved_port()
    busy = socket.socket()
    busy.bind((emb.BIND_HOST, saved))
    try:
        port = emb.resolve_port_for_serve()
    finally:
        busy.close()
    assert port != saved
    assert emb.saved_port() == port
    assert emb.endpoint().endswith(f":{port}")


def test_server_env_is_loopback_only_without_telemetry_and_has_no_foreign_secrets(
    install, tmp_path, monkeypatch
):
    data = use_data(monkeypatch, tmp_path / "a")
    emb.enable()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("CAMOFOX_API_KEY", "external-key")
    monkeypatch.setenv("SENTRY_DSN", "https://x@y/1")
    env = emb.server_env()

    assert env["CAMOFOX_BIND_HOST"] == "127.0.0.1"
    assert env["CAMOFOX_CRASH_REPORT_ENABLED"] == "false"
    assert env["CAMOFOX_ACCESS_KEY"] == emb.access_key()
    assert env["CAMOFOX_PORT"] == str(emb.saved_port())
    assert env["CAMOUFOX_INSTALL_DIR"] == str(install / "browser")
    assert env["CAMOFOX_PROFILE_DIR"] == str(data / "camofox" / "profiles")
    joined = "\n".join(env.values())
    for secret in ("tg-secret", "sk-secret", "external-key", "x@y"):
        assert secret not in joined
    assert "PROXY_HOST" not in env and "ENABLE_VNC" not in env


def test_idle_timeout_override_is_passed_only_when_numeric(install, tmp_path, monkeypatch):
    use_data(monkeypatch, tmp_path / "a")
    emb.enable()
    assert "BROWSER_IDLE_TIMEOUT_MS" not in emb.server_env()
    monkeypatch.setenv("KORRA_CAMOFOX_IDLE_TIMEOUT_MS", "15000")
    assert emb.server_env()["BROWSER_IDLE_TIMEOUT_MS"] == "15000"
    monkeypatch.setenv("KORRA_CAMOFOX_IDLE_TIMEOUT_MS", "abc")
    assert "BROWSER_IDLE_TIMEOUT_MS" not in emb.server_env()


def test_disable_removes_marker_but_keeps_key_port_and_profiles(install, tmp_path, monkeypatch):
    data = use_data(monkeypatch, tmp_path / "a")
    emb.enable()
    (data / "camofox" / "profiles").mkdir()
    key, port = emb.access_key(), emb.saved_port()
    emb.disable()
    assert not emb.is_enabled()
    assert emb.endpoint() == ""
    assert (emb.access_key(), emb.saved_port()) == (key, port)
    assert (data / "camofox" / "profiles").is_dir()


def test_status_not_installed_without_image(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "nothing"))
    use_data(monkeypatch, tmp_path / "a")
    info = emb.status()
    assert info["state"] == "not_installed"
    assert not emb.is_installed()


def test_status_running_and_not_responding(install, tmp_path, monkeypatch):
    use_data(monkeypatch, tmp_path / "a")
    emb.enable()
    monkeypatch.setattr(emb, "health", lambda timeout=2.0: True)
    assert emb.status()["state"] == "running"
    monkeypatch.setattr(emb, "health", lambda timeout=2.0: False)
    info = emb.status()
    assert info["state"] == "not_responding"
    assert "s6-svc -t /run/service/camofox" in info["next"]
    assert emb.access_key() not in str(info)


def test_health_is_false_when_nothing_listens(install, tmp_path, monkeypatch):
    use_data(monkeypatch, tmp_path / "a")
    emb.enable()
    assert emb.health(timeout=0.5) is False


def test_health_true_for_a_real_local_server(install, tmp_path, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer((emb.BIND_HOST, 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        use_data(monkeypatch, tmp_path / "a")
        emb.enable()
        emb._write_private(emb._port_file(), f"{server.server_address[1]}\n")
        assert emb.health(timeout=2) is True
        assert emb.status()["state"] == "running"
    finally:
        server.shutdown()
        server.server_close()


def test_serve_execs_node_with_clean_environment(install, tmp_path, monkeypatch):
    use_data(monkeypatch, tmp_path / "a")
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
    assert calls["args"][1].endswith("camofox-browser/server.js")
    assert "OPENAI_API_KEY" not in calls["env"]
    assert (tmp_path / "a" / "camofox" / "profiles").is_dir()


def test_cli_status_never_prints_the_key(install, tmp_path, monkeypatch, capsys):
    use_data(monkeypatch, tmp_path / "a")
    emb.enable()
    monkeypatch.setattr(emb, "health", lambda timeout=2.0: False)
    assert emb.main(["status"]) == 1
    out = capsys.readouterr()
    assert emb.access_key() not in out.out + out.err


def test_files_made_by_root_are_handed_to_the_data_owner(install, tmp_path, monkeypatch):
    data = use_data(monkeypatch, tmp_path / "a")
    owner = data.stat()
    chowned = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: chowned.append((Path(path).name, uid, gid)))
    emb.enable()
    names = {name for name, _, _ in chowned}
    assert {"camofox", "access.key.tmp", "port.tmp", "enabled.tmp"} <= names
    assert all((uid, gid) == (owner.st_uid, owner.st_gid) for _, uid, gid in chowned)
