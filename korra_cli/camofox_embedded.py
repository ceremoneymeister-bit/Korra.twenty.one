"""Встроенный Camofox («Браузер с защитой от блокировок») в коробке Korra.

Образ содержит Camofox в ``/opt/camofox`` (см. Dockerfile). Сервер запускается,
по явному выбору ``browser.cloud_provider: camofox`` или лениво после блокировки Chromium. Явный другой браузер сохраняется.
Состояние живёт в ``<корень данных>/camofox`` и переживает пересоздание контейнера:

* ``enabled`` — кэш выбора профилей, сверяется при сохранении настроек,
  создании/импорте/удалении профиля и запуске службы;
* ``fallback-requested`` — служба уже запрошена для запасного режима;
* ``disabled`` — выключатель установки (поддержка, слабый сервер);
* ``server.sock`` — Unix-сокет сервера (0600) в каталоге 0700;
* ``access.key`` — ключ доступа этой установки (0600);
* ``profiles/`` — профили браузера (вход на сайты).

Сервер слушает Unix-сокет, а не TCP-порт: контейнеры работают с
``--network host``, и любой порт на loopback виден всему хосту. Сокет лежит
в закрытом каталоге данных установки; ключ остаётся вторым рубежом.

Модуль не импортирует ничего тяжёлого на верхнем уровне: он нужен и клиенту
браузера, и ``korra doctor``. Ключ нигде не печатается.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from korra_constants import get_default_hermes_root

LOOPBACK = "127.0.0.1"
STATE_DIR_NAME = "camofox"
SOCKET_NAME = "server.sock"
S6_SERVICE_DIR = "/run/service/camofox"
_RESTART_COOLDOWN = 120.0
_RESTART_WAIT = 30.0
logger = logging.getLogger(__name__)

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


def _disabled_marker() -> Path:
    return state_dir() / "disabled"


def _key_file() -> Path:
    return state_dir() / "access.key"


def is_disabled() -> bool:
    """Владелец или поддержка выключили сервер этой установки."""
    return _disabled_marker().is_file()


def is_enabled() -> bool:
    """Сервер выбран хотя бы одним профилем и не выключен владельцем."""
    return is_installed() and (state_dir() / "enabled").is_file() and not is_disabled()


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


def _s6_svc(flag: str) -> bool:
    if not in_container():
        return False
    binary = shutil.which("s6-svc") or "/command/s6-svc"
    try:
        result = subprocess.run(
            [binary, flag, S6_SERVICE_DIR],
            check=False,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def enable() -> None:
    """Снять выключатель: s6 заметит это за несколько секунд и запустит сервер."""
    _disabled_marker().unlink(missing_ok=True)


def disable(signal: bool = True) -> None:
    """Поставить выключатель и остановить сервер (выбор браузера в профилях не меняется).

    Пока файл ``disabled`` лежит, s6 держит сервис в ожидании без Node и
    браузера. Контейнер пересоздавать не нужно.
    """
    _ensure_dir()
    marker = _disabled_marker()
    if not marker.is_file():
        _write_private(marker, "1\n")
    if signal:
        _s6_svc("-t")


def _dotenv_url() -> str:
    from korra_cli.config import load_env

    return (load_env().get("CAMOFOX_URL") or "").strip()


def explicit_url() -> str:
    """Явный внешний ``CAMOFOX_URL`` текущего профиля (пустая строка, если не задан)."""
    own = _dotenv_url()
    if own:
        return own
    try:
        from agent.secret_scope import get_secret

        return (get_secret("CAMOFOX_URL", "") or "").strip()
    except Exception:
        return (os.environ.get("CAMOFOX_URL") or "").strip()


@contextmanager
def _selection_lock(lock_path: Path, deadline: Optional[float] = None):
    """Захватить блокировку; при ошибке flock открытый файл тоже закрывается."""
    import fcntl

    _ensure_dir()
    _create_once(lock_path, "")
    with lock_path.open("r+", encoding="utf-8") as lock:
        if deadline is None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        else:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Camofox selection lock deadline") from None
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield


@contextmanager
def selection_update(*, signal: bool = True):
    """Публикация выбора и сверка службы — одна операция под selection.lock.

    Сбой блокировки не запрещает запись настроек: после неё пробуем захватить
    блокировку ещё раз для сверки. Без блокировки службу не останавливаем.
    Вне образа со встроенным Camofox запись настроек не требует блокировки.
    """
    if not is_installed():
        yield
        return
    lock_path = state_dir() / "selection.lock"
    with ExitStack() as stack:
        lock_failed = False
        try:
            stack.enter_context(_selection_lock(lock_path))
        except OSError as exc:
            lock_failed = True
            logger.warning("Camofox: не удалось захватить блокировку %s (%s)", lock_path, type(exc).__name__)

        # Исключения записи не относятся к сбою блокировки: не перехватываем
        # их и не повторяем тело with.
        yield

        try:
            if lock_failed:
                stack.enter_context(_selection_lock(lock_path))
            _sync_selection(signal=signal)
        except OSError as exc:
            if not lock_failed:  # При повторном сбое достаточно первого предупреждения.
                logger.warning("Camofox: не удалось сверить выбор профилей %s (%s)", lock_path, type(exc).__name__)


def sync(*, signal: bool = True) -> bool:
    """Сверить маркер службы с выбором профилей, не меняя их настройки."""
    if not is_installed():
        return False
    try:
        with selection_update(signal=signal):
            pass
        return (state_dir() / "enabled").is_file()
    except OSError as exc:
        logger.warning("Camofox: не удалось сверить выбор профилей (%s)", type(exc).__name__)
        return False


# Один Firefox с тремя вкладками занимает ~0,5 ГиБ cgroup, тяжёлые сайты больше:
# в контейнере меньше этого предела OOM убил бы работу агента, а не браузер.
DEFAULT_MIN_MEMORY_BYTES = 3 * 1024 ** 3
_NO_LIMIT_BYTES = 1 << 50  # cgroup v1 без лимита отдаёт число порядка 2**63
_CGROUP_MEMORY_FILES = (
    Path("/sys/fs/cgroup/memory.max"),  # cgroup v2
    Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),  # cgroup v1
)


def memory_limit_bytes() -> Optional[int]:
    """Лимит памяти контейнера из cgroup; ``None`` — без лимита или файл недоступен."""
    for path in _CGROUP_MEMORY_FILES:
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < _NO_LIMIT_BYTES:
            return int(raw)
        return None
    return None


def _gib(value: float) -> str:
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def default_memory_note() -> str:
    """Пояснение, почему неявный Camofox не включён из-за памяти, иначе ``""``."""
    limit = memory_limit_bytes()
    if limit is None or limit >= DEFAULT_MIN_MEMORY_BYTES:
        return ""
    return (
        f"Запасной Camofox выключен: контейнеру доступно {_gib(limit / 1024 ** 3)} ГиБ, "
        f"нужно от {_gib(DEFAULT_MIN_MEMORY_BYTES / 1024 ** 3)} ГиБ; можно выбрать вручную"
    )


def uses_embedded(browser: dict, *, external_url: str = "", cdp_url: str = "") -> bool:
    """One read-only choice rule for runtime, picker and service synchronization."""
    from utils import is_truthy_value

    if external_url or cdp_url or browser.get("cdp_url"):
        return False
    if is_truthy_value(browser.get("use_gateway"), default=False):
        return False
    return str(browser.get("cloud_provider") or "").strip().lower() == "camofox"


def fallback_candidate(browser: dict, *, external_url: str = "", cdp_url: str = "") -> bool:
    """Local Chromium may retry a blocked page; other backends retain their policy."""
    from utils import is_truthy_value

    return (
        not external_url and not cdp_url and not browser.get("cdp_url")
        and str(browser.get("cloud_provider") or "").strip().lower() in ("", "local")
        and browser.get("backend") in (None, "")
        and browser.get("engine") in (None, "", "auto", "chromium", "chrome")
        and not is_truthy_value(browser.get("use_gateway"), default=False)
        and not is_truthy_value(browser.get("use_real_profile"), default=False)
    )


def fallback_disabled_reason(browser: dict, *, external_url: str = "", cdp_url: str = "") -> str:
    if not is_installed():
        return "встроенный Camofox отсутствует в этой сборке"
    if not fallback_candidate(browser, external_url=external_url, cdp_url=cdp_url):
        return "выбран отдельный браузер, внешний сервер, CDP или облачный провайдер"
    if is_disabled():
        return "Camofox выключен владельцем: korra tools camofox off"
    return default_memory_note()


def start_for_fallback(deadline: float) -> bool:
    """Wake the existing s6 slot on demand, keeping the installation switch authoritative."""
    if is_disabled() or not is_installed() or socket_path_problem():
        return False
    ensure_state()
    with _selection_lock(state_dir() / "selection.lock", deadline):
        if is_disabled():
            return False
        _write_private(state_dir() / "fallback-requested", "1\n")
        _sync_selection(signal=False)
    # The slot normally sleeps in sh and notices enabled within three seconds.
    if not socket_path().exists():
        _s6_svc("-u")
    while time.monotonic() < deadline and not is_disabled():
        if health(timeout=min(1.0, deadline - time.monotonic())):
            return True
        if not in_container():
            return False
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    return False


def _sync_selection(*, signal: bool) -> None:
    """Под selection.lock читаем только файлы профилей, без process env/scope."""
    from korra_constants import named_profile_is_deleted
    root = state_dir().parent
    homes = [root]
    profiles = root / "profiles"
    if profiles.is_dir():
        homes.extend(h for h in profiles.iterdir() if h.is_dir()
                     and not h.is_symlink() and not h.name.startswith(".")
                     and not named_profile_is_deleted(h))

    def consumes(home: Path) -> bool:
        from korra_cli.config import read_user_config_raw
        import yaml

        try:
            browser = read_user_config_raw(home / "config.yaml").get("browser") or {}
            overrides = dict(
                external_url=_profile_setting(home, "CAMOFOX_URL", ()),
                cdp_url=_profile_setting(home, "BROWSER_CDP_URL", ()),
            )
            return uses_embedded(browser, **overrides) or (
                (state_dir() / "fallback-requested").is_file()
                and not fallback_disabled_reason(browser, **overrides)
            )
        except (yaml.YAMLError, TypeError, AttributeError):
            # Unknown choice is not consent to replace an explicit browser.
            return False

    wanted = any(consumes(h) for h in homes)
    marker = state_dir() / "enabled"
    was_enabled = marker.is_file()
    if wanted:
        if not was_enabled:
            _write_private(marker, "1\n")
    else:
        marker.unlink(missing_ok=True)
        if was_enabled and signal:
            _s6_svc("-t")


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


def health(timeout: float = 7.0) -> bool:
    """Readiness по сокету; обёртка проверяет context/page за ≤5 с без запуска спящего Firefox."""
    if socket_path_problem():
        return False
    conn = _UnixHTTPConnection(str(socket_path()), timeout)
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        if response.status != 200:
            return False
        payload = json.loads(response.read())
        return isinstance(payload, dict) and payload.get("ok") is True
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        conn.close()


def recover() -> bool:
    """Перезапустить зависший сервер через s6, не чаще раза в две минуты на установку.

    flock и отметка общие для всех процессов/профилей установки. Ждём новый
    сокет и readiness: зелёный ответ старого Node ещё не означает перезапуск.
    Сохранённые входы и ключ доступа не меняются.
    """
    if not is_enabled() or not in_container() or socket_path_problem():
        return False
    import fcntl

    def generation():
        try:
            info = socket_path().stat()
            return info.st_ino, info.st_mtime_ns
        except OSError:
            return None

    deadline = time.monotonic() + _RESTART_WAIT
    try:
        _ensure_dir()
        lock_path = state_dir() / "restart.lock"
        _create_once(lock_path, "0\n")
        with lock_path.open("r+") as lock:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        return False
                    time.sleep(0.1)
            if is_disabled():
                return False
            now = time.monotonic()
            try:
                previous = float(lock.read().strip())
            except ValueError:
                previous = 0.0
            if 0 < previous <= now and now - previous < _RESTART_COOLDOWN:
                logger.warning("Camofox: повторный перезапуск ограничен (120 с на установку)")
                return health()
            old_socket = generation()
            lock.seek(0)
            lock.write(f"{now}\n")
            lock.truncate()
            lock.flush()
            logger.warning("Camofox: создание контекста/вкладки недоступно; перезапуск службы через s6-svc -t")
            if not _s6_svc("-t"):
                logger.warning("Camofox: s6 не принял запрос перезапуска")
                return False
            while time.monotonic() < deadline and not is_disabled():
                current = generation()
                remaining = deadline - time.monotonic()
                if current is not None and current != old_socket and remaining > 0 and health(timeout=min(7.0, remaining)):
                    logger.warning("Camofox: служба восстановлена, повторяем запрос один раз")
                    return True
                time.sleep(0.2)
            logger.warning("Camofox: служба не восстановилась за 30 с")
    except OSError as exc:
        logger.warning("Camofox: не удалось восстановить службу (%s)", type(exc).__name__)
    return False


def _profile_setting(profile_dir: Path, env_name: str, config_keys: tuple) -> str:
    """Явное значение из .env или config.yaml указанного профиля, без смены HERMES_HOME."""
    from korra_cli.config import read_user_config_raw

    if env_name:
        from agent.secret_scope import load_env_file

        value = load_env_file(profile_dir / ".env").get(env_name, "").strip()
        if value:
            return value
    if not config_keys:
        return ""
    try:
        # Проверяем явный выбор удаляемого профиля, а не настройки активного
        # профиля или значения по умолчанию (как в profiles._read_config_model).
        node = read_user_config_raw(profile_dir / "config.yaml")
        for key in config_keys:
            node = node.get(key) if isinstance(node, dict) else None
        return str(node or "").strip()
    except Exception:
        return ""


def forget_profile_login(profile_dir: Path) -> str:
    """Удалить сохранённый вход удаляемого профиля (cookie и состояние) во встроенном сервере.

    Идентичность строится тем же способом, что в клиенте
    (``tools.browser_camofox_state.camofox_user_id``). Внешний сервер
    (``CAMOFOX_URL`` у профиля или установки) и общую идентичность,
    заданную владельцем (``CAMOFOX_USER_ID``), не трогаем. Возвращает
    удалённый каталог или пустую строку.
    """
    from tools.browser_camofox_state import camofox_user_id

    if os.environ.get("CAMOFOX_URL", "").strip() or _profile_setting(profile_dir, "CAMOFOX_URL", ()):
        return ""
    if os.environ.get("CAMOFOX_USER_ID", "").strip() or _profile_setting(
        profile_dir, "CAMOFOX_USER_ID", ("browser", "camofox", "user_id")
    ):
        return ""
    user_id = camofox_user_id(profile_dir)
    target = state_dir() / "profiles" / hashlib.sha256(user_id.encode()).hexdigest()[:32]
    # Сначала закрываем сессию, даже если каталога ещё нет: у живой сессии сервер сохранит
    # состояние сам (при закрытии пустой сессии, через минуты) и воскресит вход удалённого профиля.
    if not socket_path_problem() and is_enabled():
        conn = _UnixHTTPConnection(str(socket_path()), 5.0)
        try:
            conn.request("DELETE", f"/sessions/{user_id}", headers={"Authorization": f"Bearer {access_key()}"})
            conn.getresponse().read()
        except (OSError, http.client.HTTPException, ValueError):
            pass
        finally:
            conn.close()
    if not target.is_dir() or target.is_symlink():
        return ""
    shutil.rmtree(target, ignore_errors=True)
    return "" if target.exists() else str(target)


def status() -> Dict[str, object]:
    """Состояние для doctor: ``state``, пояснение и следующий шаг.

    state: ``not_installed`` (нет в образе), ``switched_off`` (выключатель
    установки), ``unused`` (нет явного выбора), ``running``, ``not_responding``.
    """
    if not is_installed():
        return {
            "state": "not_installed",
            "detail": "в этой сборке нет встроенного Camofox",
            "next": "используйте образ Korra с Camofox или внешний сервер (CAMOFOX_URL)",
        }
    if is_disabled():
        return {
            "state": "switched_off",
            "detail": "выключен на этой установке; запасной режим: выключен владельцем",
            "next": "включить: korra tools camofox on",
        }
    from korra_cli.config import read_raw_config_readonly
    browser = (read_raw_config_readonly() or {}).get("browser") or {}
    reason = fallback_disabled_reason(browser, external_url=explicit_url(), cdp_url=os.getenv("BROWSER_CDP_URL", ""))
    fallback = "запасной режим: выключен — " + reason if reason else "запасной режим: включён"
    if not is_enabled():
        if reason:
            return {
                "state": "unused",
                "detail": fallback,
                "next": "выбрать вручную: Инструменты → Браузер → Camofox или korra tools → Браузер → Camofox",
            }
        return {
            "state": "unused",
            "detail": "служба спит; " + fallback,
            "next": "Chromium работает первым; Camofox запустится при блокировке сайта",
        }
    problem = socket_path_problem()
    if problem:
        return {"state": "not_responding", "detail": "не может запуститься", "next": problem}
    if health():
        return {"state": "running", "detail": "работает; " + fallback, "next": ""}
    return {
        "state": "not_responding",
        "detail": "включён, но не отвечает; " + fallback,
        "next": (
            "подождите полминуты после запуска контейнера; если не проходит — запустите службу: "
            "s6-svc -u /run/service/camofox (если она уже работала, перезапустите: "
            "s6-svc -t /run/service/camofox), затем посмотрите лог контейнера (docker logs)"
        ),
    }


def tools_command(action: str) -> int:
    """``korra tools camofox {on|off|status}``: выключатель сервера этой установки."""
    if not is_installed():
        print("В этой сборке нет встроенного Camofox: переключать нечего.")
        return 1
    if action == "on":
        enable()
        sync()
        print("Выключатель встроенного Camofox снят; запасной режим доступен для Chromium, если хватает памяти.")
        print("Выбрать: Инструменты → Браузер → Camofox или korra tools → Браузер → Camofox.")
        print("Проверить: korra doctor")
        return 0
    if action == "off":
        disable()
        print("Встроенный Camofox выключен на этой установке: Node и браузер остановлены.")
        print("Профили без явного выбора браузера работают через Chromium без ошибок;")
        print("профили, которые явно выбрали Camofox, получат понятную ошибку.")
        print("Включить обратно: korra tools camofox on")
        return 0
    info = status()
    print(f"Встроенный Camofox: {info['detail']}")
    if info["next"]:
        print(info["next"])
    return 0 if info["state"] in ("running", "switched_off", "unused") else 1


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
            # One shared Firefox per installation, at most three working tabs.
            "MAX_SESSIONS": "3",
            "MAX_TABS_PER_SESSION": "3",
            "MAX_TABS_GLOBAL": "3",
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
        sync(signal=False)
        return 0
    if command == "status":
        info = status()
        print(f"{info['state']}: {info['detail']}")
        if info["next"]:
            print(info["next"])
        return 0 if info["state"] == "running" else 1
    print("usage: python -m korra_cli.camofox_embedded {serve|enable|disable|sync|status}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
