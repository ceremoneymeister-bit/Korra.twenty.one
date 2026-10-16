"""Общие помощники тестов встроенного Camofox (K21-336).

Настоящие настройки профилей, настоящий выбор браузера через ``tools_config`` и
настоящий Unix-сокет с минимальным HTTP-сервером вместо Camofox.
"""

from __future__ import annotations

import shutil
import socketserver
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from korra_cli import camofox_embedded as emb


@pytest.fixture
def install(tmp_path, monkeypatch):
    """Образ с Camofox: сервер, обёртка и бинарник на месте."""
    home = tmp_path / "opt-camofox"
    server = home.joinpath(*emb._SERVER_RELPATH)
    server.parent.mkdir(parents=True)
    server.write_text("// stub")
    (home / "unix-socket.mjs").write_text("// stub")
    (home / "browser").mkdir()
    (home / "browser" / "camoufox-bin").write_text("")
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(home))
    monkeypatch.setattr(emb, "_CGROUP_MEMORY_FILES", (tmp_path / "no-cgroup-limit",))
    return home


@pytest.fixture
def data(monkeypatch):
    """Корень данных установки. Каталог короткий: путь Unix-сокета ограничен ~107 байтами."""
    root = Path(tempfile.mkdtemp(prefix="kc", dir="/tmp"))
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("KORRA_HOME", str(root))
    for name in ("CAMOFOX_URL", "CAMOFOX_API_KEY", "BROWSER_CDP_URL", "CAMOFOX_USER_ID"):
        monkeypatch.delenv(name, raising=False)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def chromium_root(install, data):
    """Корневой профиль явно выбрал обычный Chromium, поэтому не считается потребителем по умолчанию."""
    with in_profile(data, None):
        choose_browser("Local Browser")
    return data


def use_root(monkeypatch, path: Path) -> Path:
    """Переключиться на другую установку (другой корень данных)."""
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(path))
    monkeypatch.setenv("KORRA_HOME", str(path))
    return path


def short_dir(prefix: str = "kc") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))


@contextmanager
def in_profile(root: Path, name: str | None):
    """Контекст профиля: ``None`` — корневой, иначе ``<root>/profiles/<name>``."""
    from korra_constants import reset_hermes_home_override, set_hermes_home_override

    home = root if name is None else root / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    token = set_hermes_home_override(str(home))
    try:
        yield home
    finally:
        reset_hermes_home_override(token)


def choose_browser(provider_name: str) -> None:
    """Выбрать строку «Браузер» так же, как `korra tools`: запись выбора и сохранение."""
    from korra_cli import tools_config as tc
    from korra_cli.config import save_config

    rows = {p["name"]: p for p in tc.TOOL_CATEGORIES["browser"]["providers"]}
    cfg: dict = {}
    tc._write_provider_config(rows[provider_name], cfg, managed_feature=None)
    save_config(cfg)


class UnixServer:
    """Маленький HTTP-сервер на Unix-сокете: отмечает заголовки пришедших запросов."""

    def __init__(self, path: Path, *, reply: bytes = b'{"ok": true}', respond=None):
        """``respond(method, path, body) -> (status, bytes)`` подменяет ответ на отдельные запросы."""
        self.path = Path(path)
        self.seen: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode() if length else ""
                outer.seen.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "host": self.headers.get("Host"),
                        "body": body,
                    }
                )
                status, payload = (200, reply)
                if respond is not None:
                    status, payload = respond(self.command, self.path, body) or (200, reply)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_DELETE = _handle

            def log_message(self, *args):
                pass

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self._server = Server(str(self.path), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "UnixServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()


class TcpSpy:
    """TCP-слушатель на loopback: записывает, что к нему пришло (подмена адреса установки)."""

    def __init__(self):
        self.seen: list[str | None] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                outer.seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            do_GET = do_POST = do_DELETE = _handle

            def log_message(self, *args):
                pass

        from http.server import HTTPServer

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "TcpSpy":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
