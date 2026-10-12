"""Встроенный Camofox («Браузер с защитой от блокировок») в коробке Korra.

Образ содержит Camofox в ``/opt/camofox`` (см. Dockerfile), а s6-сервис
``camofox`` запускает его только когда он нужен хотя бы одному профилю.
Состояние живёт в ``<корень данных>/camofox`` и переживает пересоздание
контейнера:

* ``enabled`` — маркер: сервис должен работать (кэш для run-скрипта s6);
* ``server.sock`` — Unix-сокет сервера (0600) в каталоге 0700;
* ``access.key`` — ключ доступа этой установки (0600);
* ``profiles/`` — профили браузера (вход на сайты).

Сервер слушает Unix-сокет, а не TCP-порт: контейнеры работают с
``--network host``, и любой порт на loopback виден всему хосту, так что чужой
процесс мог бы занять адрес установки и получить её ключ. Сокет лежит в
каталоге данных этой установки, и подключиться к нему может только её
пользователь. Ключ доступа остаётся вторым рубежом.

Camofox — браузер по умолчанию в образе: профиль, который не выбирал браузер
явно, работает через него (:func:`uses_default_browser`). Явный выбор (обычный
Chromium, облачный провайдер, ``browser.cdp_url``, внешний ``CAMOFOX_URL``)
сохраняется.

Источник правды о том, нужен ли сервер, — настройки профилей (корень и
``profiles/*``): сервер включён, пока хотя бы один профиль пользуется Camofox
(выбрал его или ничего не выбирал) без явного внешнего ``CAMOFOX_URL``. Маркер
``enabled`` — только кэш этого решения для s6 (:func:`sync` пересчитывает его,
в том числе при каждом старте сервиса).

Модуль не импортирует ничего тяжёлого на верхнем уровне: он нужен и клиенту
браузера, и run-скрипту s6, и ``korra doctor``. Ключ нигде не печатается.
"""

from __future__ import annotations

import http.client
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from korra_constants import get_default_hermes_root

LOOPBACK = "127.0.0.1"
STATE_DIR_NAME = "camofox"
SOCKET_NAME = "server.sock"
S6_SERVICE_DIR = "/run/service/camofox"

# Запросы идут по Unix-сокету, а имя хоста нужно только для заголовка Host и
# адресов requests. Зона .invalid (RFC 2606) никогда не разрешается в DNS, так
# что запрос, случайно ушедший мимо сокета, не дойдёт ни до какого сервера.
UNIX_HOST = "camofox.invalid"
BASE_URL = f"http://{UNIX_HOST}"

# sun_path в Linux — 108 байт вместе с нулевым.
MAX_SOCKET_PATH = 104

_DEFAULT_HOME = "/opt/camofox"
_SERVER_RELPATH = ("node_modules", "@askjo", "camofox-browser", "server.js")
_LAUNCHER_NAME = "unix-socket.mjs"

# Только эти переменные попадают в процесс сервера: токены агента, ключи
# провайдеров и CAMOFOX_API_KEY внешнего сервера ему не нужны.
_PASSTHROUGH_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "NODE_OPTIONS")


def install_dir() -> Path:
    return Path(os.environ.get("KORRA_CAMOFOX_HOME") or _DEFAULT_HOME)


def server_script() -> Path:
    return install_dir().joinpath(*_SERVER_RELPATH)


def launcher_script() -> Path:
    return install_dir() / _LAUNCHER_NAME


def browser_dir() -> Path:
    return install_dir() / "browser"


def is_installed() -> bool:
    """Образ содержит сервер и закреплённый бинарник Camoufox."""
    return server_script().is_file() and (browser_dir() / "camoufox-bin").is_file()


def state_dir() -> Path:
    return get_default_hermes_root() / STATE_DIR_NAME


def socket_path() -> Path:
    return state_dir() / SOCKET_NAME


def socket_path_problem() -> str:
    """Пустая строка, если путь к сокету допустим; иначе причина."""
    length = len(os.fsencode(str(socket_path())))
    if length > MAX_SOCKET_PATH:
        return (
            f"путь к сокету ({length} байт) длиннее предела Unix-сокета ({MAX_SOCKET_PATH}); "
            "каталог данных Korra нужно разместить ближе к корню"
        )
    return ""


def _enabled_marker() -> Path:
    return state_dir() / "enabled"


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
    _match_data_owner(path)
    return path


def _match_data_owner(path: Path) -> None:
    """Файлы, созданные root (docker exec), отдать владельцу каталога данных.

    Сервис работает от hermes: ключ и маркер, принадлежащие root с правами 0600,
    он бы не прочитал.
    """
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    try:
        owner = state_dir().parent.stat()
        os.chown(path, owner.st_uid, owner.st_gid)
    except OSError:
        pass


def _private_tmp(path: Path, text: str) -> Path:
    """Записать ``text`` во временный файл рядом с ``path`` (0600, имя уникально)."""
    fd, name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        _match_data_owner(tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def _write_private(path: Path, text: str) -> None:
    """Атомарно заменить файл: параллельные записи не мешают друг другу."""
    tmp = _private_tmp(path, text)
    try:
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _create_once(path: Path, text: str) -> None:
    """Создать файл, только если его ещё нет; читатель никогда не видит его неполным.

    Содержимое записывается во временный файл и публикуется жёсткой ссылкой:
    ``link`` атомарно проваливается, если файл уже создан соседом.
    """
    tmp = _private_tmp(path, text)
    try:
        os.link(tmp, path)
    except FileExistsError:
        pass
    finally:
        tmp.unlink(missing_ok=True)


def access_key() -> str:
    """Ключ этой установки; пустая строка, если он ещё не создан."""
    try:
        return _key_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_state() -> None:
    """Создать каталог состояния и ключ; существующий ключ не меняется.

    Первый ключ создаётся атомарно: одновременные включение и старт сервиса
    всегда видят один и тот же ключ.
    """
    _ensure_dir()
    if not access_key():
        _create_once(_key_file(), secrets.token_urlsafe(32) + "\n")


def endpoint() -> str:
    """Адрес встроенного сервера для клиента (запросы идут по Unix-сокету).

    Не зависит от того, запущен ли сервер сейчас: выбор встроенного режима и
    постоянная идентичность профиля не должны меняться от доступности сервера.
    """
    return BASE_URL if is_installed() else ""


def in_container() -> bool:
    return Path(S6_SERVICE_DIR).exists()


def _s6_svc(flag: str) -> None:
    if not in_container():
        return
    try:
        subprocess.run(
            ["s6-svc", flag, S6_SERVICE_DIR],
            check=False,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def enable() -> None:
    """Поставить маркер: s6 заметит его за несколько секунд и запустит сервер."""
    ensure_state()
    if not is_enabled():
        _write_private(_enabled_marker(), "1\n")


def disable(signal: bool = True) -> None:
    """Убрать маркер и остановить сервер (выбор профилей не меняется).

    ``signal=False`` — только маркер: так делает сам сервис s6 при старте,
    он не должен перезапускать сам себя.
    """
    try:
        _enabled_marker().unlink()
    except OSError:
        return
    if signal:
        _s6_svc("-t")


# ── Какие профили нуждаются в сервере ──────────────────────────────────────


def _profile_homes() -> List[Path]:
    """Корень данных и живые ``profiles/*``."""
    from korra_constants import named_profile_is_deleted

    root = state_dir().parent
    homes = [root]
    profiles = root / "profiles"
    try:
        entries = sorted(profiles.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if named_profile_is_deleted(entry):
            continue
        homes.append(entry)
    return homes


def _current_home() -> Path:
    from korra_constants import get_hermes_home

    return get_hermes_home()


def _is_deleted(home: Path) -> bool:
    from korra_constants import named_profile_is_deleted

    return named_profile_is_deleted(home)


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


class _InHome:
    """Контекст: читать настройки профиля ``home`` через обычные функции движка."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def __enter__(self) -> "_InHome":
        from korra_constants import set_hermes_home_override

        self.token = set_hermes_home_override(str(self.home))
        return self

    def __exit__(self, *exc) -> None:
        from korra_constants import reset_hermes_home_override

        reset_hermes_home_override(self.token)


def _selection(home: Path) -> Optional[str]:
    from tools.tool_backend_helpers import read_selection

    with _InHome(home):
        return read_selection("browser")


def _chose_other_browser() -> bool:
    """Текущий профиль уже определил браузер иначе, чем «ничего не выбрано».

    Явный CDP-адрес, режим Browser Use, профиль настоящего браузера, не
    автоматический движок или учётные данные облачного провайдера — это
    решения владельца, и умолчание им не мешает. Если настройки не читаются,
    считаем, что выбор мог быть, и остаёмся на прежнем поведении.
    """
    from korra_cli.config import read_raw_config_readonly

    cfg = read_raw_config_readonly()
    if getattr(cfg, "read_error", None) is not None:
        return True
    section = cfg.get("browser") if isinstance(cfg, dict) else None
    section = section if isinstance(section, dict) else {}
    if str(section.get("cdp_url") or "").strip():
        return True
    if (os.environ.get("BROWSER_CDP_URL") or "").strip():
        return True
    backend = section.get("backend")
    if isinstance(backend, str) and backend.strip().lower() not in ("", "off"):
        return True
    if section.get("use_real_profile") is True:
        return True
    for engine in (section.get("engine"), os.environ.get("AGENT_BROWSER_ENGINE")):
        if str(engine or "").strip().lower() not in ("", "auto"):
            return True
    try:
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
        from plugins.browser.browserbase.provider import BrowserbaseBrowserProvider

        return BrowserUseBrowserProvider().is_available() or BrowserbaseBrowserProvider().is_available()
    except Exception:
        return True


def uses_default_browser() -> bool:
    """В образе есть Camofox, и текущий профиль не выбирал браузер: работает умолчание.

    Явный внешний ``CAMOFOX_URL`` здесь не учитывается: он решает, какой
    сервер использовать (см. :func:`explicit_url`), а не выбирает ли профиль
    Camofox.
    """
    if not is_installed():
        return False
    from tools.tool_backend_helpers import read_selection

    if read_selection("browser") is not None:
        return False
    return not _chose_other_browser()


def _uses_default_browser_in(home: Path) -> bool:
    with _InHome(home):
        return uses_default_browser()


def _dotenv_url(home: Path) -> str:
    from korra_cli.config import load_env

    with _InHome(home):
        return (load_env().get("CAMOFOX_URL") or "").strip()


def explicit_url(home: Path, current: Optional[Path] = None) -> str:
    """Явный внешний ``CAMOFOX_URL`` профиля ``home`` (пустая строка, если не задан).

    Берётся из ``.env`` профиля. Значение из окружения процесса относится к
    профилю, в котором процесс запущен, и к контейнеру в целом; для чужого
    профиля оно засчитывается, только если не совпадает с ``.env`` текущего
    (иначе это просто загруженный ``.env`` текущего профиля).
    """
    current = current or _current_home()
    own = _dotenv_url(home)
    if own:
        return own
    from_process = ""
    try:
        from agent.secret_scope import get_secret

        from_process = (get_secret("CAMOFOX_URL", "") or "").strip()
    except Exception:
        from_process = (os.environ.get("CAMOFOX_URL") or "").strip()
    if not from_process:
        return ""
    if _same_dir(home, current):
        return from_process
    return "" if from_process == _dotenv_url(current) else from_process


def current_explicit_url() -> str:
    """Явный внешний ``CAMOFOX_URL`` текущего профиля (пустая строка, если не задан)."""
    return explicit_url(_current_home())


def wants_embedded(home: Path, current: Optional[Path] = None) -> bool:
    """Профиль пользуется Camofox (выбрал или не выбирал ничего) и не указал внешний сервер."""
    if explicit_url(home, current):
        return False
    return _selection(home) == "camofox" or _uses_default_browser_in(home)


def consumers(current_selected: Optional[bool] = None) -> List[str]:
    """Профили, которым нужен встроенный сервер (имена: ``default`` и из ``profiles/``).

    ``current_selected`` — выбор текущего профиля, который ещё не записан на
    диск (его передаёт код, меняющий настройку): он заменяет прочитанное.
    """
    current = _current_home()
    homes = _profile_homes()
    if not any(_same_dir(h, current) for h in homes) and not _is_deleted(current):
        homes.append(current)
    root = state_dir().parent
    names: List[str] = []
    for home in homes:
        if current_selected is not None and _same_dir(home, current):
            wanted = bool(current_selected) and not explicit_url(home, current)
        else:
            wanted = wants_embedded(home, current)
        if wanted:
            names.append("default" if _same_dir(home, root) else home.name)
    return names


def sync(current_selected: Optional[bool] = None, signal: bool = True) -> bool:
    """Привести сервер в соответствие с настройками профилей; True, если он нужен.

    Включён, пока хотя бы один профиль пользуется Camofox без внешнего адреса;
    выключается, когда потребителей не осталось.
    """
    if not is_installed():
        return False
    wanted = bool(consumers(current_selected))
    if wanted:
        enable()
    else:
        disable(signal=signal)
    return wanted


# ── Состояние ──────────────────────────────────────────────────────────────


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__(UNIX_HOST, timeout=timeout)
        self._socket_path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._socket_path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


def unix_session():
    """``requests.Session``, который ходит на ``BASE_URL`` по Unix-сокету сервера.

    Без новой зависимости: подменяется только способ открыть соединение. Адрес
    сокета берётся при каждом подключении, прокси из окружения отключены.
    """
    import requests
    from requests.adapters import BaseAdapter, HTTPAdapter
    from urllib3.connection import HTTPConnection
    from urllib3.connectionpool import HTTPConnectionPool

    class _Connection(HTTPConnection):
        def _new_conn(self):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            timeout = self.timeout if isinstance(self.timeout, (int, float)) else None
            sock.settimeout(timeout)
            try:
                sock.connect(str(socket_path()))
            except BaseException:
                sock.close()
                raise
            return sock

    class _Pool(HTTPConnectionPool):
        ConnectionCls = _Connection

    class _Adapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = {"http": _Pool, "https": _Pool}

    class _Refuse(BaseAdapter):
        # Всё, что не адрес сервера (например, редирект наружу), не отправляется:
        # ключ доступа не должен уйти ни на какой TCP-адрес.
        def send(self, request, **kwargs):
            raise requests.ConnectionError("встроенный Camofox доступен только по Unix-сокету")

        def close(self):
            pass

    session = requests.Session()
    session.trust_env = False
    session.mount("http://", _Refuse())
    session.mount("https://", _Refuse())
    session.mount(BASE_URL, _Adapter())
    return session


def health(timeout: float = 2.0) -> bool:
    """Сервер отвечает на /health по сокету (маршрут открыт без ключа)."""
    if socket_path_problem():
        return False
    conn = _UnixHTTPConnection(str(socket_path()), timeout)
    try:
        conn.request("GET", "/health")
        return conn.getresponse().status == 200
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        conn.close()


def status() -> Dict[str, object]:
    """Состояние для doctor и панели: ``state``, пояснение и следующий шаг.

    state: ``not_installed`` (нет в образе), ``disabled`` (все профили выбрали
    другой браузер), ``selected_off`` (нужен профилям, но сервер выключен),
    ``unused`` (сервер включён, но профилей-потребителей нет), ``running``,
    ``not_responding``.
    """
    if not is_installed():
        return {
            "state": "not_installed",
            "detail": "в этой сборке нет встроенного Camofox",
            "next": "используйте образ Korra с Camofox или внешний сервер (CAMOFOX_URL)",
            "consumers": [],
        }
    users = consumers()
    base: Dict[str, object] = {"consumers": users}
    if not is_enabled():
        if users:
            return {
                **base,
                "state": "selected_off",
                "detail": "нужен профилям (по умолчанию или по выбору), но сервер выключен",
                "next": "вернуть сервер: korra doctor --fix",
            }
        return {
            **base,
            "state": "disabled",
            "detail": "выключен: все профили выбрали другой браузер",
            "next": "включить: korra tools → Браузер → «Браузер с защитой от блокировок»",
        }
    problem = socket_path_problem()
    if problem:
        return {**base, "state": "not_responding", "detail": "включён, но не может запуститься", "next": problem}
    if not users:
        return {
            **base,
            "state": "unused",
            "detail": "включён, но ни одному профилю он не нужен",
            "next": "выключить лишний сервер: korra doctor --fix",
        }
    if health():
        return {**base, "state": "running", "detail": "работает", "next": ""}
    return {
        **base,
        "state": "not_responding",
        "detail": "включён, но не отвечает",
        "next": (
            "подождите полминуты после включения; если не проходит — запустите службу: "
            "s6-svc -u /run/service/camofox (если она уже работала, перезапустите: "
            "s6-svc -t /run/service/camofox), затем посмотрите лог контейнера (docker logs)"
        ),
    }


def server_env() -> Dict[str, str]:
    """Окружение процесса сервера: белый список плюс настройки встроенного режима."""
    env = {name: os.environ[name] for name in _PASSTHROUGH_ENV if os.environ.get(name)}
    root = state_dir()
    env.update(
        {
            "CAMOFOX_UNIX_SOCKET": str(socket_path()),
            # Запасной рубеж: обёртка не даёт серверу открыть TCP, но если бы
            # дала, он слушал бы только loopback.
            "CAMOFOX_BIND_HOST": LOOPBACK,
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
    return ["node", str(launcher_script()), str(server_script())]


def serve() -> None:
    """Заменить процесс на сервер Camofox (вызывается из run-скрипта s6)."""
    if not is_installed() or not launcher_script().is_file():
        print("[camofox] сервер не установлен в образ", file=sys.stderr)
        raise SystemExit(1)
    problem = socket_path_problem()
    if problem:
        print(f"[camofox] {problem}", file=sys.stderr)
        raise SystemExit(1)
    ensure_state()
    env = server_env()
    for sub in ("profiles", "cookies", "traces", "uploads"):
        (state_dir() / sub).mkdir(parents=True, exist_ok=True)
    # Устаревший сокет после аварийного завершения мешает bind.
    try:
        socket_path().unlink()
    except FileNotFoundError:
        pass
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
    if command == "sync":
        # Вызывается из run-скрипта s6: сам себя сервис перезапускать не должен.
        print("нужен" if sync(signal=False) else "не нужен")
        return 0
    if command == "status":
        info = status()
        print(f"{info['state']}: {info['detail']}")
        if info["next"]:
            print(info["next"])
        return 0 if info["state"] in ("running", "disabled") else 1
    print("usage: python -m korra_cli.camofox_embedded {serve|enable|disable|sync|status}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
