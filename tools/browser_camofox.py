"""Camofox browser backend — local anti-detection browser via REST API.

Camofox-browser is a self-hosted Node.js server wrapping Camoufox (Firefox
fork with C++ fingerprint spoofing).  It exposes a REST API that maps 1:1
to our browser tool interface: accessibility snapshots with element refs,
click/type/scroll by ref, screenshots, etc.

When ``CAMOFOX_URL`` is set (e.g. ``http://localhost:9377``), the browser
tools route through this module instead of the ``agent-browser`` CLI.

Setup::

    # Option 1: npm
    git clone https://github.com/jo-inc/camofox-browser && cd camofox-browser
    npm install && npm start   # downloads Camoufox (~300MB) on first run

    # Option 2: Docker
    docker run -p 9377:9377 -e CAMOFOX_PORT=9377 jo-inc/camofox-browser

Then set ``CAMOFOX_URL=http://localhost:9377`` in ``~/.hermes/.env``.
For Docker Camofox, optionally set ``CAMOFOX_REWRITE_LOOPBACK_URLS=true``
so page URLs like ``http://127.0.0.1:3000`` are opened inside the
container as ``http://host.docker.internal:3000``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Optional
from urllib.parse import SplitResult, urlsplit, urlunsplit

import requests

from agent.secret_scope import get_secret
from korra_cli.config import cfg_get, load_config, read_raw_config
from tools.browser_camofox_state import get_camofox_identity
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_fallback_deadline: ContextVar[Optional[float]] = ContextVar("camofox_fallback_deadline", default=None)

_DEFAULT_TIMEOUT = 30  # fallback when config is unreadable
_SNAPSHOT_MAX_CHARS = 80_000  # camofox paginates at this limit
_vnc_url: Optional[str] = None  # cached from /health response
_vnc_url_checked = False  # only probe once per process

# Cached command timeout from config (resolved lazily, like browser_tool)
_cached_cmd_timeout: Optional[int] = None
_cmd_timeout_resolved = False


def _get_command_timeout() -> int:
    """Return ``browser.command_timeout`` from config, falling back to 30s.

    Mirrors :func:`tools.browser_tool._get_command_timeout` so both the
    local browser path and the Camofox path honour the same config knob.
    Result is cached after the first call.
    """
    global _cached_cmd_timeout, _cmd_timeout_resolved
    if _cmd_timeout_resolved:
        return _cached_cmd_timeout  # type: ignore[return-value]

    _cmd_timeout_resolved = True
    result = _DEFAULT_TIMEOUT
    try:
        cfg = read_raw_config()
        val = cfg_get(cfg, "browser", "command_timeout")
        if val is not None:
            result = max(int(val), 5)  # floor at 5s
    except Exception as exc:
        logger.debug("Could not read browser.command_timeout: %s", exc)
    _cached_cmd_timeout = result
    return result


def _explicit_camofox_url() -> str:
    return (get_secret("CAMOFOX_URL", "") or "").rstrip("/")


def _embedded_camofox_endpoint() -> str:
    """Address of the Camofox server bundled in the Korra image, or ``""``.

    Available as a spare for local Chromium or when this profile selected Camofox,
    unless an explicit browser/CDP/external URL overrides it. The address and
    saved identity do not depend on server availability;
    an installation switched off by its owner gives a clear error.
    """
    try:
        from korra_cli import camofox_embedded
        from korra_cli.config import read_raw_config_readonly

        endpoint = camofox_embedded.endpoint()
        if not endpoint:
            return ""
        cfg = read_raw_config_readonly() or {}
        browser = cfg.get("browser") or {}
        overrides = dict(external_url=_explicit_camofox_url(), cdp_url=os.getenv("BROWSER_CDP_URL", "").strip())
        if camofox_embedded.uses_embedded(browser, **overrides) or camofox_embedded.fallback_candidate(browser, **overrides):
            return endpoint
        return ""
    except Exception:  # pragma: no cover — helpers are in-repo
        return ""


def _using_embedded_camofox() -> bool:
    return not _explicit_camofox_url() and bool(_embedded_camofox_endpoint())


_FALLBACK_NOTE = (
    "Встроенный браузер с защитой от блокировок сейчас не отвечает, поэтому страница открыта обычным "
    "Chromium (входы из Camofox в него не переносятся). Автоматическое восстановление не помогло; "
    "повторите позже или выполните `korra doctor`."
)
_HEALTH_TTL = 5.0
_FAILURE_TTL = 30.0
# (действует до, ключ сокета, сервер отвечает)
_health_cache: tuple = (0.0, "", True)
_health_lock = threading.Lock()


def _remember_health(key: str, ok: bool, ttl: float) -> None:
    global _health_cache
    with _health_lock:
        _health_cache = (time.monotonic() + ttl, key, ok)


def _embedded_unavailable() -> bool:
    """The bundled server is the active browser, but it does not answer.

    Then the browser tools run through ordinary Chromium instead of failing:
    a crash or a slow start of the bundled server must not leave agents
    without a browser. The probe is ``/health`` over the socket (it tests
    context/page creation without launching an idle Firefox), reused for a few
    seconds. A call that finds the server unusable despite a good probe
    (connection lost, no tab can be opened) calls :func:`_mark_embedded_down`,
    which turns the cached answer to "unavailable" for longer. An explicit
    external ``CAMOFOX_URL`` is never replaced, and a server switched off by
    the owner is not a failure: explicit Camofox then gets a clear error.
    """
    if _explicit_camofox_url():
        return False
    try:
        from korra_cli import camofox_embedded

        if camofox_embedded.is_disabled():
            return False
        key = str(camofox_embedded.socket_path())
        with _health_lock:
            expires, cached_key, cached_ok = _health_cache
            if cached_key == key and time.monotonic() < expires:
                return not cached_ok
        ok = camofox_embedded.health() or camofox_embedded.recover()
        _remember_health(key, ok, _HEALTH_TTL)
        if not ok:
            logger.warning("Camofox: readiness не восстановлена; используем Chromium без профильных входов")
        return not ok
    except Exception:  # pragma: no cover — helpers are in-repo
        return False


def _mark_embedded_down(ttl: float = _HEALTH_TTL) -> None:
    """Forget a good probe: the server failed a real call, so the browser tools use Chromium for ``ttl`` seconds."""
    try:
        from korra_cli import camofox_embedded

        _remember_health(str(camofox_embedded.socket_path()), False, ttl)
    except Exception:  # pragma: no cover — helpers are in-repo
        pass


def embedded_fallback_note() -> str:
    """Short note for the agent when this call went to Chromium for lack of the bundled server."""
    if os.getenv("BROWSER_CDP_URL", "").strip() or _config_cdp_url():
        return ""
    from tools.tool_backend_helpers import read_selection
    if read_selection("browser") == "camofox" and _using_embedded_camofox() and _embedded_unavailable():
        return _FALLBACK_NOTE
    return ""


_unix_session: Optional[requests.Session] = None
_unix_session_lock = threading.Lock()


def _embedded_session() -> requests.Session:
    """Shared session that reaches the bundled server over its Unix socket."""
    global _unix_session
    with _unix_session_lock:
        if _unix_session is None:
            from korra_cli import camofox_embedded

            _unix_session = camofox_embedded.unix_session()
        return _unix_session


def _auth_headers() -> Dict[str, str]:
    """Return the Authorization header for the Camofox server in use.

    Bundled server: the per-installation access key. External server:
    ``CAMOFOX_API_KEY`` when set.
    """
    if _using_embedded_camofox():
        from korra_cli import camofox_embedded

        key = camofox_embedded.access_key()
    else:
        key = (get_secret("CAMOFOX_API_KEY", "") or "").strip()
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def get_camofox_url() -> str:
    """Return the Camofox server URL (explicit, else bundled), or empty string."""
    return _explicit_camofox_url() or _embedded_camofox_endpoint()


def _config_cdp_url() -> str:
    """Persistent ``browser.cdp_url`` from config.yaml, or empty string.

    Read here (instead of importing ``browser_tool._get_cdp_override`` to avoid
    a circular import) so Camofox can yield to a config-based CDP override the
    same way it already yields to the ``BROWSER_CDP_URL`` env override.
    """
    try:
        from korra_cli.config import read_raw_config

        browser_cfg = read_raw_config().get("browser", {})
        if isinstance(browser_cfg, dict):
            return str(browser_cfg.get("cdp_url", "") or "").strip()
    except Exception:
        pass
    return ""


def is_camofox_mode() -> bool:
    """True when the Camofox backend is selected and no CDP override is active.

    In the Korra image Chromium is primary unless Camofox was explicitly selected.
    If an explicitly selected bundled server does not answer,
    this is False for the call and the browser tools use Chromium instead.

    Camofox is a selection: ``browser.cloud_provider: camofox`` (set via
    ``hermes tools``). ``CAMOFOX_URL`` is the server ADDRESS only — its
    presence no longer selects the backend when a different
    ``browser.cloud_provider`` is stored. Legacy read-time interpretation:
    when NO cloud provider selection was ever written, a set ``CAMOFOX_URL``
    keeps activating Camofox exactly as before (nothing is migrated/written
    to config).

    A CDP override takes priority over Camofox so the browser tools operate on
    the real CDP browser (and a CDP backend is treated as non-local for SSRF
    checks) instead of being silently routed to Camofox. The override may come
    from the ``BROWSER_CDP_URL`` env var (set by ``/browser connect``) OR a
    persistent ``browser.cdp_url`` in config.yaml — both are honored, matching
    ``browser_tool._get_cdp_override()``'s precedence.
    """
    if os.getenv("BROWSER_CDP_URL", "").strip():
        return False
    if _config_cdp_url():
        return False
    try:
        from tools.tool_backend_helpers import read_selection

        selected = read_selection("browser")
    except Exception:  # pragma: no cover — helpers are in-repo
        selected = None
    if selected is not None and selected != "camofox":
        # An explicit different browser selection wins: CAMOFOX_URL is just
        # an address, not a choice.
        return False
    if selected is None and not _explicit_camofox_url():
        # The bundled endpoint is available as a spare, never the implicit primary.
        return False
    # Bundled server that does not answer: ordinary Chromium takes the call.
    return not (_using_embedded_camofox() and _embedded_unavailable())


def check_camofox_available() -> bool:
    """Verify the Camofox server is reachable."""
    global _vnc_url, _vnc_url_checked
    url = get_camofox_url()
    if not url:
        return False
    try:
        embedded = _using_embedded_camofox()
        resp = _request("get", f"{url}/health", timeout=7 if embedded else 5)
        if embedded:
            _vnc_url_checked = True
            data = resp.json()
            return resp.status_code == 200 and isinstance(data, dict) and data.get("ok") is True
        if resp.status_code == 200 and not _vnc_url_checked:
            try:
                data = resp.json()
                vnc_port = data.get("vncPort")
                if isinstance(vnc_port, int) and 1 <= vnc_port <= 65535:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    host = parsed.hostname or "localhost"
                    _vnc_url = f"http://{host}:{vnc_port}"
            except (ValueError, KeyError):
                pass
            _vnc_url_checked = True
        return resp.status_code == 200
    except Exception:
        return False


def get_vnc_url() -> Optional[str]:
    """Return the VNC URL if the Camofox server exposes one, or None."""
    if not _vnc_url_checked:
        check_camofox_available()
    return _vnc_url


def _get_camofox_config() -> Dict[str, Any]:
    """Return the ``browser.camofox`` config block, or an empty dict."""
    try:
        camofox_cfg = load_config().get("browser", {}).get("camofox", {})
    except Exception as exc:
        logger.warning("camofox config check failed, defaulting to disabled: %s", exc)
        return {}
    return camofox_cfg if isinstance(camofox_cfg, dict) else {}


def _managed_persistence_enabled() -> bool:
    """Return whether Hermes-managed persistence is enabled for Camofox.

    When enabled, sessions use a stable profile-scoped userId so the
    Camofox server can map it to a persistent browser profile directory.
    When disabled (default), each session gets a random userId (ephemeral).

    Controlled by ``browser.camofox.managed_persistence`` in config.yaml.
    """
    return _persistence_for(_get_camofox_config())


def _persistence_for(camofox_cfg: Dict[str, Any]) -> bool:
    """Bundled server always keeps per-profile logins; otherwise follow config."""
    return bool(camofox_cfg.get("managed_persistence")) or _using_embedded_camofox()


def _camofox_identity_override(task_id: Optional[str], camofox_cfg: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Return an externally configured Camofox identity, if one is set.

    Integrations that own the visible Camofox browser can set a shared user ID
    so Hermes operates in the same browser profile instead of creating a
    separate private session.
    """
    user_id = (
        (get_secret("CAMOFOX_USER_ID", "") or "").strip()
        or str(camofox_cfg.get("user_id") or "").strip()
    )
    if not user_id:
        return None

    session_key = (
        (get_secret("CAMOFOX_SESSION_KEY", "") or "").strip()
        or str(camofox_cfg.get("session_key") or "").strip()
        or f"task_{(task_id or 'default')[:16]}"
    )
    return {"user_id": user_id, "session_key": session_key}


def _env_flag(name: str) -> Optional[bool]:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    logger.debug("Ignoring invalid boolean env %s=%r", name, raw)
    return None


def _adopt_existing_tab_enabled(camofox_cfg: Dict[str, Any]) -> bool:
    """Return whether Hermes should recover an existing Camofox tab ID."""
    env_value = _env_flag("CAMOFOX_ADOPT_EXISTING_TAB")
    if env_value is not None:
        return env_value
    return bool(camofox_cfg.get("adopt_existing_tab"))


def _loopback_rewrite_enabled(camofox_cfg: Dict[str, Any]) -> bool:
    """Return whether loopback navigation URLs should be rewritten for Docker.

    ``CAMOFOX_URL`` itself often points at a host-published Docker port such as
    ``http://127.0.0.1:9377``.  That is correct for Hermes talking to the
    Camofox control API, but a page URL like ``http://127.0.0.1:3000`` is opened
    by the browser *inside* the Docker container.  In that context loopback
    points at the container, not the host running the web app.

    The rewrite is opt-in because non-Docker Camofox installs run the browser on
    the host, where loopback URLs are already correct.
    """
    env_value = _env_flag("CAMOFOX_REWRITE_LOOPBACK_URLS")
    if env_value is not None:
        return env_value
    return bool(camofox_cfg.get("rewrite_loopback_urls"))


def _loopback_rewrite_host(camofox_cfg: Dict[str, Any]) -> str:
    """Return the host alias used when rewriting loopback page URLs."""
    return (
        os.getenv("CAMOFOX_LOOPBACK_HOST_ALIAS", "").strip()
        or str(camofox_cfg.get("loopback_host_alias") or "").strip()
        or "host.docker.internal"
    )


def _is_loopback_hostname(hostname: Optional[str]) -> bool:
    """Return True for localhost/127.0.0.0/8/::1-style hostnames."""
    if not hostname:
        return False
    host = hostname.strip().strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _rewrite_loopback_url_for_camofox(url: str) -> tuple[str, Optional[Dict[str, str]]]:
    """Rewrite loopback page URLs for Docker-hosted Camofox, if configured.

    Returns ``(rewritten_url, metadata)``.  ``metadata`` is present only when a
    rewrite happened so the tool result can disclose the change to the model.
    """
    camofox_cfg = _get_camofox_config()
    if not _loopback_rewrite_enabled(camofox_cfg):
        return url, None

    try:
        parsed = urlsplit(url)
    except ValueError:
        return url, None

    if parsed.scheme not in {"http", "https"} or not _is_loopback_hostname(parsed.hostname):
        return url, None

    alias = _loopback_rewrite_host(camofox_cfg)
    if not alias:
        return url, None

    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    host_part = f"[{alias}]" if ":" in alias and not alias.startswith("[") else alias
    port_part = f":{parsed.port}" if parsed.port else ""
    rewritten = urlunsplit(
        SplitResult(parsed.scheme, f"{userinfo}{host_part}{port_part}", parsed.path, parsed.query, parsed.fragment)
    )
    return rewritten, {
        "from": parsed.hostname or "",
        "to": alias,
        "original_url": url,
        "rewritten_url": rewritten,
    }


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------
# Maps (profile home, task_id) -> {"user_id": str, "tab_id": str|None}
_sessions: Dict[tuple, Dict[str, Any]] = {}
_sessions_lock = threading.Lock()


def _adopt_existing_tab(session: Dict[str, Any]) -> Dict[str, Any]:
    """Attach process-local state to an already-open managed Camofox tab.

    Some integrations own the visible Camofox tab outside Hermes. Gateway
    restarts can leave this module's in-memory session cache empty even though
    Camofox still has that tab, so rehydrate tab_id before creating a new tab.
    """
    if session.get("tab_id") or not session.get("adopt_existing_tab"):
        return session

    if not get_camofox_url():
        return session

    try:
        tabs = _get("/tabs", params={"userId": session["user_id"]}, timeout=5).get("tabs", [])
    except Exception as exc:
        logger.debug("Camofox tab adoption failed for %s: %s", session.get("user_id"), exc)
        return session

    if not isinstance(tabs, list) or not tabs:
        return session

    session_key = session.get("session_key")
    matching_tabs = [
        tab
        for tab in tabs
        if isinstance(tab, dict) and tab.get("listItemId") == session_key
    ]
    candidates = matching_tabs or [tab for tab in tabs if isinstance(tab, dict)]
    latest = candidates[-1] if candidates else None
    tab_id = latest.get("tabId") if isinstance(latest, dict) else None
    if isinstance(tab_id, str) and tab_id:
        session["tab_id"] = tab_id
        logger.debug("Adopted existing Camofox tab %s for %s", tab_id, session.get("user_id"))

    return session


@contextmanager
def _session_guard():
    deadline = _fallback_deadline.get()
    if deadline is None:
        acquired = _sessions_lock.acquire()
    else:
        acquired = _sessions_lock.acquire(timeout=max(0, deadline - time.monotonic()))
    if not acquired:
        raise requests.Timeout("Camofox session lock deadline")
    try:
        yield
    finally:
        _sessions_lock.release()


def _get_session(task_id: Optional[str]) -> Dict[str, Any]:
    """Get or create a camofox session for the given task.

    When managed persistence is enabled, uses a deterministic userId
    derived from the Hermes profile so the Camofox server can map it
    to the same persistent browser profile across restarts.
    """
    task_id = task_id or "default"
    # Camofox bypasses agent-browser's _get_session_info; it still needs the
    # same profile-aware inactivity/shutdown tracking as every other backend.
    from tools.browser_tool import _start_browser_cleanup_thread, _update_session_activity

    _update_session_activity(task_id)
    _start_browser_cleanup_thread()
    from korra_constants import get_hermes_home

    cache_key = (str(get_hermes_home()), task_id)
    with _session_guard():
        if cache_key in _sessions:
            return _adopt_existing_tab(_sessions[cache_key])

        _require_embedded_ready()
        camofox_cfg = _get_camofox_config()
        identity_override = _camofox_identity_override(task_id, camofox_cfg)
        if identity_override:
            session = {
                "user_id": identity_override["user_id"],
                "tab_id": None,
                "session_key": identity_override["session_key"],
                "managed": True,
                "adopt_existing_tab": _adopt_existing_tab_enabled(camofox_cfg),
            }
        elif _persistence_for(camofox_cfg):
            identity = get_camofox_identity(task_id)
            session = {
                "user_id": identity["user_id"],
                "tab_id": None,
                "session_key": identity["session_key"],
                "managed": True,
                "adopt_existing_tab": _adopt_existing_tab_enabled(camofox_cfg),
            }
        else:
            session = {
                "user_id": f"hermes_{uuid.uuid4().hex[:10]}",
                "tab_id": None,
                "session_key": f"task_{task_id[:16]}",
                "managed": False,
                "adopt_existing_tab": False,
            }
        _sessions[cache_key] = session
        return _adopt_existing_tab(session)


def _ensure_tab(task_id: Optional[str], url: Optional[str] = None) -> Dict[str, Any]:
    """Ensure a tab exists for the session, creating one if needed."""
    session = _get_session(task_id)
    if session["tab_id"]:
        return session
    base = get_camofox_url()
    for attempt in range(2):
        try:
            resp = _request(
                "post",
                f"{base}/tabs",
                json={
                    "userId": session["user_id"],
                    "listItemId": session["session_key"],
                    **({"url": url} if url else {}),
                },
                timeout=_get_command_timeout(),
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            break
        except (requests.Timeout, requests.HTTPError, requests.ConnectionError) as exc:
            if not _using_embedded_camofox():
                raise
            if _fallback_deadline.get() is not None:
                raise  # one bounded attempt; no recovery probes or Chromium cascade
            if attempt == 0:
                if not _server_cannot_open_tab(session, exc):
                    raise
                from korra_cli import camofox_embedded

                if camofox_embedded.recover():
                    _remember_health(str(camofox_embedded.socket_path()), True, _HEALTH_TTL)
                    continue
            logger.warning("Camofox: создание вкладки не восстановилось; используем Chromium без профильных входов")
            _mark_embedded_down(_FAILURE_TTL)
            raise CamofoxCannotOpenTab(_CANNOT_OPEN_TAB_TEXT) from None
    data = resp.json()
    session["tab_id"] = data.get("tabId")
    session["url"] = data.get("url") or url
    session["http_status"] = data.get("httpStatus")
    return session


def _server_cannot_open_tab(session: Dict[str, Any], exc: Exception) -> bool:
    """A 5xx or timeout while opening a tab: is the server broken, or was it the page?

    The same request also navigates to the target URL, so a dead site looks the
    same as a browser that does not start. A blank tab tells them apart: if it
    opens, the server is fine and the original error stands.
    """
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        if status < 500:
            return False
        try:
            if exc.response.json().get("code") == "admission_rejected":
                return False
        except (ValueError, AttributeError):
            pass
    try:
        probe = _request(
            "post",
            f"{get_camofox_url()}/tabs",
            json={"userId": session["user_id"], "listItemId": session["session_key"]},
            timeout=15,
            headers=_auth_headers(),
        )
        probe.raise_for_status()
    except requests.RequestException:
        return True
    try:
        tab_id = probe.json().get("tabId")
        if tab_id:
            _delete(f"/tabs/{tab_id}", {"userId": session["user_id"]}, timeout=10)
    except Exception:
        pass
    return False


def _drop_session(task_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Remove and return session info."""
    from korra_constants import get_hermes_home

    cache_key = (str(get_hermes_home()), task_id or "default")
    with _sessions_lock:
        return _sessions.pop(cache_key, None)


def camofox_soft_cleanup(task_id: Optional[str] = None) -> bool:
    """Release the in-memory session without destroying the server-side context.

    When managed persistence is enabled the browser profile (and its cookies)
    must survive across agent tasks.  This helper drops only the local tracking
    entry and returns ``True``.  When managed persistence is *not* enabled it
    does nothing and returns ``False`` so the caller can fall back to
    :func:`camofox_close`.
    """
    camofox_cfg = _get_camofox_config()
    if _persistence_for(camofox_cfg) or _camofox_identity_override(task_id, camofox_cfg):
        _drop_session(task_id)
        logger.debug("Camofox soft cleanup for task %s (managed persistence)", task_id)
        return True
    return False


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

class CamofoxUnreachable(requests.ConnectionError):
    """Bundled Camofox did not answer; the message is meant for the agent."""


_CANNOT_OPEN_TAB_TEXT = (
    "Браузер с защитой от блокировок не может открыть вкладку после попытки автоматического восстановления. "
    "Повторите позже; если не проходит, выполните `korra doctor` и посмотрите лог контейнера "
    "или временно выберите обычный браузер в `korra tools`."
)


class CamofoxCannotOpenTab(CamofoxUnreachable):
    """The bundled server answers but cannot open a tab (browser did not start, out of memory)."""


def _require_embedded_ready() -> None:
    """Bundled Camofox selected but not answering: stop before any session exists.

    The profile's login is tied to its identity, which the server keeps. If the
    server is down the agent gets a plain explanation and a next step instead of
    a session that would later fail or lose the saved login.
    """
    if not _using_embedded_camofox():
        return
    from korra_cli import camofox_embedded

    if camofox_embedded.is_disabled():
        raise CamofoxUnreachable(_connection_error_text())
    if _fallback_deadline.get() is not None:
        return  # readiness was checked within the shared fallback deadline
    if not (camofox_embedded.health() or camofox_embedded.recover()):
        _mark_embedded_down()
        raise CamofoxUnreachable(_connection_error_text())


class CamofoxStaleTab(RuntimeError):
    """The page expired; the model must navigate and obtain fresh element refs."""


def _request(method: str, *args: Any, **kwargs: Any) -> requests.Response:
    embedded = _using_embedded_camofox()
    deadline = _fallback_deadline.get()
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout("Camofox fallback deadline exceeded")
        # urllib3 total bounds connect + read for each step of this attempt.
        from urllib3.util import Timeout
        kwargs["timeout"] = Timeout(total=remaining, connect=min(5.0, remaining))
    try:
        if embedded:
            response = _embedded_session().request(method.upper(), *args, **kwargs)
        else:
            response = getattr(requests, method)(*args, **kwargs)
        if response.status_code in (404, 410):
            from urllib.parse import urlsplit

            parts = urlsplit(str(args[0])).path.split("/")
            if len(parts) >= 4 and parts[1] == "tabs":
                with _sessions_lock:
                    for session in _sessions.values():
                        if session.get("tab_id") == parts[2]:
                            session["tab_id"] = None
                            if parts[3] != "navigate":
                                url = session.get("url")
                                target = f" to {url}" if url else " to the task's last URL"
                                raise CamofoxStaleTab(
                                    "Browser tab expired during inactivity. Call browser_navigate"
                                    + target + ", then take a fresh snapshot before continuing; old element refs are invalid."
                                )
        return response
    except requests.ConnectionError:
        if embedded:
            _mark_embedded_down()
            raise CamofoxUnreachable(_connection_error_text()) from None
        raise


def _post(path: str, body: dict, timeout: Optional[int] = None) -> dict:
    """POST JSON to camofox and return parsed response."""
    if timeout is None:
        timeout = _get_command_timeout()
    url = f"{get_camofox_url()}{path}"
    resp = _request("post", url, json=body, timeout=timeout, headers=_auth_headers())
    resp.raise_for_status()
    return resp.json()


def _get(path: str, params: dict = None, timeout: Optional[int] = None) -> dict:
    """GET from camofox and return parsed response."""
    if timeout is None:
        timeout = _get_command_timeout()
    url = f"{get_camofox_url()}{path}"
    resp = _request("get", url, params=params, timeout=timeout, headers=_auth_headers())
    resp.raise_for_status()
    return resp.json()


def _get_raw(path: str, params: dict = None, timeout: Optional[int] = None) -> requests.Response:
    """GET from camofox and return raw response (for binary data)."""
    if timeout is None:
        timeout = _get_command_timeout()
    url = f"{get_camofox_url()}{path}"
    resp = _request("get", url, params=params, timeout=timeout, headers=_auth_headers())
    resp.raise_for_status()
    return resp


def _delete(path: str, body: dict = None, timeout: Optional[int] = None) -> dict:
    """DELETE to camofox and return parsed response."""
    if timeout is None:
        timeout = _get_command_timeout()
    url = f"{get_camofox_url()}{path}"
    resp = _request("delete", url, json=body, timeout=timeout, headers=_auth_headers())
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _connection_error_text() -> str:
    """Plain-words explanation for an unreachable Camofox server (no stack, no secrets)."""
    if _using_embedded_camofox():
        from korra_cli import camofox_embedded

        if camofox_embedded.is_disabled():
            return (
                "Браузер с защитой от блокировок выбран, но его встроенный сервер выключен на этой установке. "
                "Попросите владельца выполнить `korra tools camofox on` (вход на сайтах сохранится) "
                "или выбрать обычный браузер в `korra tools`."
            )
        return (
            "Браузер с защитой от блокировок не отвечает: встроенный сервер включён, "
            "но не принимает соединения. Он мог ещё запускаться или упасть. "
            "Подождите полминуты и повторите; если не проходит, попросите владельца "
            "выполнить `korra doctor` (там указан следующий шаг) или временно выбрать "
            "обычный браузер в `korra tools`."
        )
    return (
        f"Cannot connect to Camofox at {get_camofox_url()}. "
        "Is the server running? Start with: npm start (in camofox-browser dir) "
        "or: docker run -p 9377:9377 -e CAMOFOX_PORT=9377 jo-inc/camofox-browser"
    )


def camofox_navigate(url: str, task_id: Optional[str] = None) -> str:
    """Navigate to a URL via Camofox."""
    try:
        browser_url, rewrite_info = _rewrite_loopback_url_for_camofox(url)
        session = _get_session(task_id)
        if not session["tab_id"]:
            # Create tab with the target URL directly
            session = _ensure_tab(task_id, browser_url)
            data = {"ok": True, "url": session.get("url") or browser_url, "httpStatus": session.get("http_status")}
        else:
            # Navigate existing tab — recover from stale tab 404
            try:
                data = _post(
                    f"/tabs/{session['tab_id']}/navigate",
                    {"userId": session["user_id"], "url": browser_url},
                    timeout=60,
                )
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (404, 410):
                    logger.warning(
                        "Camofox tab expired (%s); creating a fresh tab.",
                        e.response.status_code,
                    )
                    session["tab_id"] = None
                    session = _ensure_tab(task_id, browser_url)
                    data = {"ok": True, "url": session.get("url") or browser_url, "httpStatus": session.get("http_status")}
                else:
                    raise
        session["url"] = data.get("url", browser_url)
        from tools.browser_tool import _pin_page_browser

        _pin_page_browser(task_id, "camofox")
        result = {
            "success": True,
            "url": session["url"],
            "title": data.get("title", ""),
        }
        if data.get("httpStatus") is not None:
            result["status"] = data["httpStatus"]
        if rewrite_info:
            result["requested_url"] = url
            result["url_rewrite"] = rewrite_info
            result["warning"] = (
                "Rewrote loopback URL for Docker-hosted Camofox: "
                f"{rewrite_info['from']} -> {rewrite_info['to']}"
            )
        vnc = get_vnc_url()
        if vnc:
            result["vnc_url"] = vnc
            result["vnc_hint"] = (
                "Browser is visible via VNC. "
                "Share this link with the user so they can watch the browser live."
            )

        # Auto-take a compact snapshot so the model can act immediately
        try:
            snap_data = _get(
                f"/tabs/{session['tab_id']}/snapshot",
                params={"userId": session["user_id"]},
            )
            snapshot_text = snap_data.get("snapshot", "")
            from tools.browser_tool import (
                get_browser_snapshot_threshold,
                _truncate_snapshot,
            )
            threshold = get_browser_snapshot_threshold()
            if len(snapshot_text) > threshold:
                snapshot_text = _truncate_snapshot(snapshot_text, max_chars=threshold)
            result["snapshot"] = snapshot_text
            result["element_count"] = snap_data.get("refsCount", 0)
        except Exception:
            pass  # Navigation succeeded; snapshot is a bonus

        return json.dumps(result)
    except requests.HTTPError as e:
        detail = str(e)
        try:
            payload = e.response.json()
            if payload.get("code") == "admission_rejected" or e.response.status_code == 429:
                detail = payload.get("error", detail) + ". Close unused browser sessions or wait for idle cleanup, then navigate again."
        except (ValueError, AttributeError):
            pass
        return tool_error(f"Navigation failed: {detail}", success=False)
    except requests.ConnectionError:
        fallback = _navigate_with_chromium(url, task_id)
        if fallback is not None:
            return fallback
        return json.dumps({
            "success": False,
            "error": _connection_error_text(),
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def _navigate_with_chromium(url: str, task_id: Optional[str]) -> Optional[str]:
    """The bundled server failed this call: open the page in ordinary Chromium instead.

    Only for a server that is on but broken (connection lost, no tab can be
    opened). The failed call has already marked the server as down, so the
    browser tools route to Chromium; the page open in Camofox is not carried
    over. Returns ``None`` when the call must fail as before: external server,
    server switched off by the owner (explicit Camofox then gets a clear error).
    """
    if _fallback_deadline.get() is not None or not _using_embedded_camofox():
        return None
    from korra_cli import camofox_embedded

    if camofox_embedded.is_disabled() or is_camofox_mode():
        return None
    from tools.browser_tool import _browser_navigate

    logger.warning("Camofox недоступен после попытки восстановления; страница открывается в Chromium без профильных входов")
    return _browser_navigate(url, task_id)


def _camofox_private_page_block(session: Dict[str, Any], task_id: Optional[str], action: str) -> Optional[str]:
    """Return a blocked payload when the current Camofox page is private/internal.

    Mirrors the eval-path guard added for ``_camofox_eval`` (browser_tool.py):
    Camofox snapshot / vision / image-extraction all read current page state, so
    on a non-local backend they can leak the content of an intranet/metadata
    page the terminal itself can't reach.  The gate matches ``browser_snapshot``
    / ``browser_vision`` — only active when the SSRF guard applies (non-local
    backend, not a local sidecar, ``allow_private_urls`` unset).  Fail-open on
    probe failure, matching the sibling guards.

    Imports are deferred to call time because ``browser_tool`` imports this
    module; importing it at module load would create a circular import.
    """
    from tools.browser_tool import (
        _camofox_current_page_private_url,
        _eval_ssrf_guard_active,
    )

    if not _eval_ssrf_guard_active(task_id or "default"):
        return None
    blocked_url = _camofox_current_page_private_url(session["tab_id"], session["user_id"])
    if not blocked_url:
        return None
    return json.dumps({
        "success": False,
        "error": (
            "Blocked: page URL targets a private or internal address "
            f"({blocked_url}). Refusing to {action} on this page in this "
            "browser mode."
        ),
    }, ensure_ascii=False)


def camofox_snapshot(full: bool = False, task_id: Optional[str] = None,
                     user_task: Optional[str] = None) -> str:
    """Get accessibility tree snapshot from Camofox.

    ``user_task`` is deprecated and ignored — oversized snapshots always
    truncate-and-store (no LLM summarization), same as the main browser tool.
    """
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        blocked = _camofox_private_page_block(session, task_id, "read a page snapshot")
        if blocked:
            return blocked

        data = _get(
            f"/tabs/{session['tab_id']}/snapshot",
            params={"userId": session["user_id"]},
        )

        snapshot = data.get("snapshot", "")
        refs_count = data.get("refsCount", 0)

        # Same truncate-and-store handling as the main browser tool: cut at
        # line boundaries, store the full tree to cache/web, append a
        # read_file pointer.
        from tools.browser_tool import (
            get_browser_snapshot_threshold,
            _truncate_snapshot,
        )

        threshold = get_browser_snapshot_threshold()
        if len(snapshot) > threshold:
            snapshot = _truncate_snapshot(snapshot, max_chars=threshold)

        return json.dumps({
            "success": True,
            "snapshot": snapshot,
            "element_count": refs_count,
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_click(ref: str, task_id: Optional[str] = None) -> str:
    """Click an element by ref via Camofox."""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        blocked = _camofox_private_page_block(session, task_id, "click")
        if blocked:
            return blocked

        # Strip @ prefix if present (our tool convention)
        clean_ref = ref.lstrip("@")

        data = _post(
            f"/tabs/{session['tab_id']}/click",
            {"userId": session["user_id"], "ref": clean_ref},
        )
        return json.dumps({
            "success": True,
            "clicked": clean_ref,
            "url": data.get("url", ""),
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_type(ref: str, text: str, task_id: Optional[str] = None) -> str:
    """Type text into an element by ref via Camofox."""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        blocked = _camofox_private_page_block(session, task_id, "type")
        if blocked:
            return blocked

        clean_ref = ref.lstrip("@")

        _post(
            f"/tabs/{session['tab_id']}/type",
            {"userId": session["user_id"], "ref": clean_ref, "text": text},
        )
        from agent.display import (
            redact_browser_typed_text_for_display,
            redact_tool_args_for_display,
        )

        display_text = (redact_tool_args_for_display("browser_type", {"text": text}) or {})["text"]

        response = {
            "success": True,
            # Match browser_tool.browser_type: run typed text through the
            # secret-pattern redactor so API keys / tokens don't leak into
            # tool progress or chat history.  The raw text is still typed into
            # the page; only the returned display value is redacted.
            "typed": display_text,
            "element": clean_ref,
        }
        response = redact_browser_typed_text_for_display(response, text)
        return json.dumps(response)
    except Exception as e:
        from agent.display import redact_browser_typed_text_for_display

        return tool_error(redact_browser_typed_text_for_display(str(e), text), success=False)


def camofox_scroll(direction: str, task_id: Optional[str] = None) -> str:
    """Scroll the page via Camofox."""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        _post(
            f"/tabs/{session['tab_id']}/scroll",
            {"userId": session["user_id"], "direction": direction},
        )
        return json.dumps({"success": True, "scrolled": direction})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_back(task_id: Optional[str] = None) -> str:
    """Navigate back via Camofox."""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        data = _post(
            f"/tabs/{session['tab_id']}/back",
            {"userId": session["user_id"]},
        )
        return json.dumps({"success": True, "url": data.get("url", "")})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_press(key: str, task_id: Optional[str] = None) -> str:
    """Press a keyboard key via Camofox."""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        blocked = _camofox_private_page_block(session, task_id, "press")
        if blocked:
            return blocked

        _post(
            f"/tabs/{session['tab_id']}/press",
            {"userId": session["user_id"], "key": key},
        )
        return json.dumps({"success": True, "pressed": key})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_close(task_id: Optional[str] = None) -> str:
    """Close the browser session via Camofox."""
    try:
        session = _drop_session(task_id)
        if not session:
            return json.dumps({"success": True, "closed": True})

        _delete(
            f"/sessions/{session['user_id']}",
        )
        return json.dumps({"success": True, "closed": True})
    except Exception as e:
        return json.dumps({"success": True, "closed": True, "warning": str(e)})


def camofox_get_images(task_id: Optional[str] = None) -> str:
    """Get images on the current page via Camofox.

    Extracts image information from the accessibility tree snapshot,
    since Camofox does not expose a dedicated /images endpoint.
    """
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        blocked = _camofox_private_page_block(session, task_id, "extract page images")
        if blocked:
            return blocked

        import re

        data = _get(
            f"/tabs/{session['tab_id']}/snapshot",
            params={"userId": session["user_id"]},
        )
        snapshot = data.get("snapshot", "")

        # Parse img elements from the accessibility tree.
        # Format: img "alt text" or img "alt text" [eN]
        # URLs appear on /url: lines following img entries
        images = []
        lines = snapshot.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("- img ", "img ")):
                alt_match = re.search(r'img\s+"([^"]*)"', stripped)
                alt = alt_match.group(1) if alt_match else ""
                # Look for URL on the next line
                src = ""
                if i + 1 < len(lines):
                    url_match = re.search(r'/url:\s*(\S+)', lines[i + 1].strip())
                    if url_match:
                        src = url_match.group(1)
                if alt or src:
                    images.append({"src": src, "alt": alt})

        return json.dumps({
            "success": True,
            "images": images,
            "count": len(images),
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_vision(question: str, annotate: bool = False,
                   task_id: Optional[str] = None) -> str:
    """Take a screenshot and analyze it with vision AI via Camofox."""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        blocked = _camofox_private_page_block(session, task_id, "capture a screenshot")
        if blocked:
            return blocked

        # Get screenshot as binary PNG
        resp = _get_raw(
            f"/tabs/{session['tab_id']}/screenshot",
            params={"userId": session["user_id"]},
        )

        # Save screenshot to cache
        from korra_constants import get_hermes_home
        screenshots_dir = get_hermes_home() / "browser_screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(screenshots_dir / f"browser_screenshot_{uuid.uuid4().hex[:8]}.png")

        with open(screenshot_path, "wb") as f:
            f.write(resp.content)

        # Encode for vision LLM
        img_b64 = base64.b64encode(resp.content).decode("utf-8")

        # Also get annotated snapshot if requested
        annotation_context = ""
        if annotate:
            try:
                snap_data = _get(
                    f"/tabs/{session['tab_id']}/snapshot",
                    params={"userId": session["user_id"]},
                )
                annotation_context = f"\n\nAccessibility tree (element refs for interaction):\n{snap_data.get('snapshot', '')[:3000]}"
            except Exception:
                pass

        # Redact secrets from annotation context before sending to vision LLM.
        # The screenshot image itself cannot be redacted, but at least the
        # text-based accessibility tree snippet won't leak secret values.
        from agent.redact import redact_sensitive_text
        annotation_context = redact_sensitive_text(annotation_context)

        # Send to vision LLM
        from agent.auxiliary_client import call_llm

        vision_prompt = (
            f"Analyze this browser screenshot and answer: {question}"
            f"{annotation_context}"
        )

        try:
            _cfg = load_config()
            _vision_cfg = cfg_get(_cfg, "auxiliary", "vision", default={})
            _vision_timeout = float(_vision_cfg.get("timeout", 120))
            _vision_temperature = float(_vision_cfg.get("temperature", 0.1))
        except Exception:
            _vision_timeout = 120.0
            _vision_temperature = 0.1

        response = call_llm(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": vision_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_b64}",
                        },
                    },
                ],
            }],
            task="vision",
            temperature=_vision_temperature,
            timeout=_vision_timeout,
        )
        analysis = (response.choices[0].message.content or "").strip() if response.choices else ""

        # Redact secrets the vision LLM may have read from the screenshot.
        from agent.redact import redact_sensitive_text
        analysis = redact_sensitive_text(analysis)

        return json.dumps({
            "success": True,
            "analysis": analysis,
            "screenshot_path": screenshot_path,
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_console(clear: bool = False, task_id: Optional[str] = None) -> str:
    """Get console output — limited support in Camofox.

    Camofox does not expose browser console logs via its REST API.
    Returns an empty result with a note.
    """
    return json.dumps({
        "success": True,
        "console_messages": [],
        "js_errors": [],
        "total_messages": 0,
        "total_errors": 0,
        "note": "Console log capture is not available with the Camofox backend. "
                "Use browser_snapshot or browser_vision to inspect page state.",
    })


