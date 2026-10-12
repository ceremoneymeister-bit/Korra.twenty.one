"""Встроенный Camofox («Браузер с защитой от блокировок») в коробке Korra.

Образ содержит Camofox в ``/opt/camofox`` (см. Dockerfile), а s6-сервис
``camofox`` запускает его только когда владелец включил этот браузер.
Состояние живёт в ``<корень данных>/camofox`` и переживает пересоздание
контейнера:

* ``enabled`` — маркер: сервис должен работать;
* ``port`` — порт на 127.0.0.1, выбранный случайно и свободный на хосте;
* ``access.key`` — ключ доступа этой установки (0600);
* ``profiles/`` — профили браузера (вход на сайты).

Порт и ключ свои у каждой установки: контейнеры работают с ``--network host``,
и общий порт 9377 дал бы конфликт, а общий loopback позволил бы одной установке
управлять браузером другой и видеть её сохранённые входы.

Модуль не импортирует ничего тяжёлого: он нужен и клиенту браузера, и
run-скрипту s6, и ``korra doctor``. Ключ нигде не печатается.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from korra_constants import get_default_hermes_root

BIND_HOST = "127.0.0.1"
PORT_RANGE = (20000, 29999)
STATE_DIR_NAME = "camofox"
S6_SERVICE_DIR = "/run/service/camofox"

_DEFAULT_HOME = "/opt/camofox"
_SERVER_RELPATH = ("node_modules", "@askjo", "camofox-browser", "server.js")

# Только эти переменные попадают в процесс сервера: токены агента, ключи
# провайдеров и CAMOFOX_API_KEY внешнего сервера ему не нужны.
_PASSTHROUGH_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "NODE_OPTIONS")


def install_dir() -> Path:
    return Path(os.environ.get("KORRA_CAMOFOX_HOME") or _DEFAULT_HOME)


def server_script() -> Path:
    return install_dir().joinpath(*_SERVER_RELPATH)


def browser_dir() -> Path:
    return install_dir() / "browser"


def is_installed() -> bool:
    """Образ содержит сервер и закреплённый бинарник Camoufox."""
    return server_script().is_file() and (browser_dir() / "camoufox-bin").is_file()


def state_dir() -> Path:
    return get_default_hermes_root() / STATE_DIR_NAME


def _enabled_marker() -> Path:
    return state_dir() / "enabled"


def _port_file() -> Path:
    return state_dir() / "port"


def _key_file() -> Path:
    return state_dir() / "access.key"


def is_enabled() -> bool:
    return _enabled_marker().is_file()


def _ensure_dir() -> Path:
    path = state_dir()
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _port_is_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((BIND_HOST, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _pick_free_port() -> int:
    low, high = PORT_RANGE
    for _ in range(200):
        port = low + secrets.randbelow(high - low + 1)
        if _port_is_free(port):
            return port
    raise RuntimeError("не нашёл свободный порт для встроенного Camofox")


def saved_port() -> Optional[int]:
    try:
        port = int(_port_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def access_key() -> str:
    """Ключ этой установки; пустая строка, если он ещё не создан."""
    try:
        return _key_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_state() -> None:
    """Создать ключ и закрепить порт; существующие значения не меняются."""
    _ensure_dir()
    if not access_key():
        _write_private(_key_file(), secrets.token_urlsafe(32) + "\n")
    if saved_port() is None:
        _write_private(_port_file(), f"{_pick_free_port()}\n")


def resolve_port_for_serve() -> int:
    """Порт для запуска сервера.

    Сохранённый порт могли занять после перезагрузки хоста; тогда выбираем
    другой и перезаписываем файл, чтобы клиент пошёл по новому адресу.
    """
    ensure_state()
    port = saved_port()
    if port is not None and _port_is_free(port):
        return port
    port = _pick_free_port()
    _write_private(_port_file(), f"{port}\n")
    return port


def endpoint() -> str:
    """Адрес встроенного сервера или пустая строка, если он не включён."""
    if not is_enabled():
        return ""
    port = saved_port()
    return f"http://{BIND_HOST}:{port}" if port else ""


def in_container() -> bool:
    return Path(S6_SERVICE_DIR).exists()


def _nudge_service() -> None:
    """Попросить s6 перезапустить сервис, чтобы он заметил смену маркера."""
    if not in_container():
        return
    try:
        subprocess.run(
            ["s6-svc", "-t", S6_SERVICE_DIR],
            check=False,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def enable() -> None:
    ensure_state()
    _write_private(_enabled_marker(), "1\n")


def disable() -> None:
    try:
        _enabled_marker().unlink()
    except FileNotFoundError:
        return
    except OSError:
        return
    _nudge_service()


def health(timeout: float = 2.0) -> bool:
    """Сервер отвечает на /health (маршрут открыт без ключа)."""
    url = endpoint()
    if not url:
        return False
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=timeout) as resp:
            return resp.status == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def status() -> Dict[str, str]:
    """Состояние для doctor и панели: ``state`` и пояснение со следующим шагом.

    state: ``not_installed`` (нет в образе), ``disabled``, ``running``,
    ``not_responding``.
    """
    if not is_installed():
        return {
            "state": "not_installed",
            "detail": "в этой сборке нет встроенного Camofox",
            "next": "используйте образ Korra с Camofox или внешний сервер (CAMOFOX_URL)",
        }
    if not is_enabled():
        return {
            "state": "disabled",
            "detail": "выключен",
            "next": "включить: korra tools → Браузер → «Браузер с защитой от блокировок»",
        }
    if health():
        return {"state": "running", "detail": "работает", "next": ""}
    return {
        "state": "not_responding",
        "detail": "включён, но не отвечает",
        "next": (
            "подождите полминуты после включения; если не проходит — "
            "перезапустите сервис: s6-svc -t /run/service/camofox, "
            "затем посмотрите лог контейнера (docker logs)"
        ),
    }


def server_env() -> Dict[str, str]:
    """Окружение процесса сервера: белый список плюс настройки встроенного режима."""
    env = {name: os.environ[name] for name in _PASSTHROUGH_ENV if os.environ.get(name)}
    root = state_dir()
    env.update(
        {
            "CAMOFOX_PORT": str(resolve_port_for_serve()),
            "CAMOFOX_BIND_HOST": BIND_HOST,
            "CAMOFOX_ACCESS_KEY": access_key(),
            "CAMOFOX_CRASH_REPORT_ENABLED": "false",
            "CAMOFOX_PROFILE_DIR": str(root / "profiles"),
            "CAMOFOX_COOKIES_DIR": str(root / "cookies"),
            "CAMOFOX_TRACES_DIR": str(root / "traces"),
            "CAMOFOX_UPLOADS_DIR": str(root / "uploads"),
            "CAMOUFOX_INSTALL_DIR": str(browser_dir()),
            "NODE_ENV": "production",
        }
    )
    idle = os.environ.get("KORRA_CAMOFOX_IDLE_TIMEOUT_MS", "").strip()
    if idle.isdigit():
        env["BROWSER_IDLE_TIMEOUT_MS"] = idle
    return env


def serve_command() -> List[str]:
    return ["node", str(server_script())]


def serve() -> None:
    """Заменить процесс на сервер Camofox (вызывается из run-скрипта s6)."""
    if not is_installed():
        print("[camofox] сервер не установлен в образ", file=sys.stderr)
        raise SystemExit(1)
    ensure_state()
    env = server_env()
    for sub in ("profiles", "cookies", "traces", "uploads"):
        (state_dir() / sub).mkdir(parents=True, exist_ok=True)
    os.chdir(str(server_script().parent))
    os.execvpe("node", serve_command(), env)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "status"
    if command == "serve":
        serve()
        return 0
    if command == "enable":
        enable()
        return 0
    if command == "disable":
        disable()
        return 0
    if command == "status":
        info = status()
        print(f"{info['state']}: {info['detail']}")
        if info["next"]:
            print(info["next"])
        return 0 if info["state"] in ("running", "disabled") else 1
    print("usage: python -m korra_cli.camofox_embedded {serve|enable|disable|status}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
