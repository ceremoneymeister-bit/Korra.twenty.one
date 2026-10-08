"""Read-only access to the owner's CRM: Битрикс24 and amoCRM.

One installation has one CRM connection (:mod:`korra_cli.crm_connection`); the
dashboard card and every agent read through the two readers below. Both carry
the limits of the clients that already served customers in agent ``.env``
files (``bitrix_readonly.py`` and ``amo.py``) and share these rules:

* **Only reading.** Bitrix24 methods come from a fixed allowlist, amoCRM
  requests are ``GET`` on a fixed set of ``/api/v4`` paths. A method or path
  outside the lists raises before anything is sent, so a write is not
  expressible even with a webhook or token that allows it.
* **Own portal only.** The address is parsed once; the host must be the
  vendor's own domain, the scheme HTTPS, and a redirect is never followed.
* **Bounded.** Request count per run, pages per list, bytes per answer,
  seconds per request and the pace of requests are capped.
* **No secrets in errors.** Failures are reported as a code (``CrmError``);
  nothing from the request line, the webhook or the token is ever put in a
  message, and answers are scrubbed of webhook-looking URLs and secret keys.

The network sits behind a ``transport`` callable so tests (and the fixture
that closes the network) never reach a real portal.
"""

from __future__ import annotations

import copy
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib import parse

MAX_REQUESTS = 120
MAX_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT = 20.0
ADDRESS_TIMEOUT = 8.0
HANDSHAKE_ATTEMPTS = 3
BITRIX_INTERVAL = 0.55
AMO_INTERVAL = 0.26
RATE_PAUSE_SECONDS = 60.0

# Bitrix24 cloud: one account label under the vendor's own domains. Box
# (self-hosted) Bitrix24 on a customer's domain is not supported.
BITRIX_CLOUD_SUFFIXES = (
    "bitrix24.ru", "bitrix24.com", "bitrix24.by", "bitrix24.kz", "bitrix24.ua", "bitrix24.uz",
    "bitrix24.de", "bitrix24.es", "bitrix24.fr", "bitrix24.it", "bitrix24.pl", "bitrix24.eu",
    "bitrix24.in", "bitrix24.cn", "bitrix24.vn", "bitrix24.la", "bitrix24.tr", "bitrix24.id",
    "bitrix24.co.uk", "bitrix24.com.br", "bitrix24.com.tr", "bitrix24.com.vn",
)
BITRIX_HOST = re.compile(
    r"^[a-z0-9][a-z0-9-]{0,62}\.(?:" + "|".join(re.escape(x) for x in BITRIX_CLOUD_SUFFIXES) + r")$"
)
_DNS_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\.)+[a-z]{2,24}$")
BITRIX_PATH = re.compile(r"^/rest/\d{1,12}/[A-Za-z0-9_-]{8,64}/?$")
AMO_HOST = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\.(?:amocrm\.ru|amocrm\.com|kommo\.com)$")

BITRIX_ALLOWED = frozenset(
    "scope profile crm.lead.list crm.lead.get crm.lead.fields crm.deal.list crm.deal.get "
    "crm.deal.fields crm.deal.productrows.get crm.contact.list crm.contact.get "
    "crm.company.list crm.company.get crm.status.list crm.category.list "
    "crm.dealcategory.list crm.dealcategory.stage.list crm.activity.list crm.activity.get "
    "crm.timeline.comment.list crm.stagehistory.list tasks.task.list tasks.task.get user.get".split()
)
BITRIX_LISTS = frozenset(m for m in BITRIX_ALLOWED if m.endswith(".list") or m == "user.get")

_AMO_PATHS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^/api/v4/account$",
        r"^/api/v4/leads$",
        r"^/api/v4/leads/\d{1,12}$",
        r"^/api/v4/leads/\d{1,12}/notes$",
        r"^/api/v4/leads/pipelines$",
        r"^/api/v4/leads/unsorted$",
        r"^/api/v4/contacts/\d{1,12}$",
        r"^/api/v4/users$",
        r"^/api/v4/tasks$",
        r"^/api/v4/events$",
    )
)

_SECRET_KEY = re.compile(r"(?i)(token|secret|password|webhook|call_record_url|download_url)")
_WEBHOOK_URL = re.compile(r"https?://[^\s<>\"']*/rest/\d+/[^\s<>\"']+")
_SIGNED_URL = re.compile(
    r"https?://[^\s<>\"']*[?&](?:auth|token|signature|sig|access_token|X-Amz-Signature)=[^\s<>\"']*",
    re.I,
)

BITRIX = "bitrix24"
AMOCRM = "amocrm"
SOURCE_LABELS = {BITRIX: "Битрикс24", AMOCRM: "amoCRM"}


class CrmError(RuntimeError):
    """A failed CRM read. ``code`` is one of :data:`ERROR_CODES`; the text is ours."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


# code -> (title, text). ``{host}`` is the portal; texts never carry remote data.
ERROR_TEXTS: dict[str, tuple[str, str]] = {
    "bad_url": (
        "Не тот адрес",
        "Адрес не похож на адрес вашего портала. Скопируйте его целиком из CRM, как в подсказке выше.",
    ),
    "bad_key": (
        "Ключ не подошёл",
        "{source} не принимает этот ключ: он неверный, отозван или скопирован не полностью. "
        "Создайте новый ключ по подсказке и вставьте его ещё раз.",
    ),
    "forbidden": (
        "Не хватает прав",
        "У ключа нет доступа к сделкам. Откройте права ключа в {source}, включите чтение CRM и нажмите «Проверить» ещё раз.",
    ),
    "plan_closed": (
        "На тарифе закрыт доступ для приложений",
        "{source} не открывает доступ для приложений на тарифе этого аккаунта. "
        "Для Битрикс24 это подписка «Маркетплейс» (раздел «Тарифы» портала), для amoCRM — действующая оплата аккаунта. "
        "Подключите её и нажмите «Проверить» ещё раз.",
    ),
    "network": (
        "{source} не отвечает",
        "С сервера Korra не удаётся дозвониться до {host}. Если ключ уже сохранён — это ошибка сети, а не ключа: "
        "карточка повторит проверку позже. Если не пройдёт до вечера, напишите в поддержку Korra.",
    ),
    "rate_limited": (
        "Слишком частые запросы",
        "{source} просит подождать: запросов к CRM слишком много. Карточка повторит попытку через несколько минут.",
    ),
    "self_hosted": (
        "Коробочный Битрикс24 не поддерживается",
        "Подключение работает с облачным Битрикс24 (адрес вида компания.bitrix24.ru). "
        "Коробочная версия на собственном домене пока не поддерживается.",
    ),
    "protocol": (
        "Неожиданный ответ",
        "Адрес отвечает, но это не {source}. Проверьте, что скопировали адрес вебхука или аккаунта именно этой CRM.",
    ),
    "not_connected": (
        "CRM не подключена",
        "Подключение CRM делается в карточке «Продажи» на главной странице Korra.",
    ),
    "limit": (
        "Слишком много данных",
        "Ответ CRM слишком большой или запросов потребовалось больше предела. Данные показаны частично.",
    ),
}
ERROR_CODES = frozenset(ERROR_TEXTS) | {"no_tasks", "not_allowed", "redirect"}


def describe_error(code: str, source: str = BITRIX, host: str = "") -> dict[str, Any]:
    """Russian title and text for ``code``; safe to show to the owner."""
    code = {"redirect": "bad_url", "not_allowed": "protocol", "budget": "limit"}.get(code, code)
    title, text = ERROR_TEXTS.get(code, ERROR_TEXTS["protocol"])
    fields = {"source": SOURCE_LABELS.get(source, "CRM"), "host": host or "портала"}
    return {
        "code": code,
        "title": title.format(**fields),
        "message": text.format(**fields),
        "retry": code in {"network", "rate_limited"},
    }


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


#: ``transport(method, url, headers, body) -> Response``; raises ``OSError`` on network trouble.
Transport = Callable[[str, str, dict, Optional[bytes]], Response]


def scrub(value: Any) -> Any:
    """Drop secret-looking keys and webhook/signed URLs from a CRM answer."""
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if _SECRET_KEY.search(str(k)) else scrub(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        value = _WEBHOOK_URL.sub("[REDACTED_WEBHOOK]", value)
        value = _SIGNED_URL.sub("[REDACTED_SIGNED_URL]", value)
    return value


# ---------------------------------------------------------------- pace

_pace_lock = threading.Lock()
_last_call: dict[str, float] = {}
_paused_until: dict[str, float] = {}


def reset_pace() -> None:
    with _pace_lock:
        _last_call.clear()
        _paused_until.clear()


def _wait_turn(host: str, interval: float, sleep: Callable[[float], None], now: Callable[[], float]) -> None:
    """In-process spacing of requests to one portal, shared by the card and agents."""
    with _pace_lock:
        until = _paused_until.get(host, 0.0)
        if until > now():
            raise CrmError("rate_limited")
        wait = _last_call.get(host, 0.0) + interval - now()
        _last_call[host] = now() + max(0.0, wait)
    if wait > 0:
        sleep(wait)


def _pause(host: str, seconds: float, now: Callable[[], float]) -> None:
    with _pace_lock:
        _paused_until[host] = now() + max(1.0, min(seconds, 600.0))


# ---------------------------------------------------------------- Bitrix24


def parse_bitrix_webhook(value: str) -> tuple[str, str]:
    """``(host, base_url)`` for an inbound-webhook address, or raise ``bad_url``."""
    text = (value or "").strip()
    try:
        parts = parse.urlsplit(text)
        port = parts.port
    except ValueError:
        raise CrmError("bad_url") from None
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or port not in (None, 443)
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or not BITRIX_PATH.fullmatch(parts.path)
    ):
        raise CrmError("bad_url")
    if not BITRIX_HOST.fullmatch(host):
        raise CrmError("self_hosted" if _DNS_HOST.fullmatch(host) else "bad_url")
    return host, f"https://{host}{parts.path.rstrip('/')}/"


def _flatten(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _flatten(v, f"{prefix}[{k}]" if prefix else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, "" if value is None else str(value)


def _validate_bitrix(method: str, params: Any) -> None:
    if method not in BITRIX_ALLOWED:
        raise CrmError("not_allowed", method)
    if not isinstance(params, dict) or any(
        str(k).lower() in {"auth", "cmd", "method", "url", "access_token"} for k in params
    ):
        raise CrmError("not_allowed", "params")


def is_global_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global


def _require_global_peer(sock: socket.socket) -> None:
    """Refuse a connection that actually landed on a private/loopback/link-local address."""
    try:
        peer = sock.getpeername()[0]
    except OSError:
        peer = ""
    if not is_global_address(str(peer)):
        sock.close()
        raise CrmError("bad_url", "address")


class _VendorHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS that checks the address it really connected to before any TLS or secret is sent."""

    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), self.timeout)
        try:
            _require_global_peer(sock)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]
        except BaseException:
            sock.close()
            raise


def default_bitrix_transport(method: str, url: str, headers: dict, body: Optional[bytes]) -> Response:
    """HTTPS without redirects, with a byte cap. Raises ``OSError`` on network trouble."""
    parts = parse.urlsplit(url)
    conn = _VendorHTTPSConnection(
        parts.hostname, parts.port or 443, timeout=REQUEST_TIMEOUT, context=ssl.create_default_context()
    )
    try:
        conn.request(method, parts.path + (f"?{parts.query}" if parts.query else ""), body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read(MAX_BYTES + 1)
        return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, raw)
    except http.client.HTTPException as exc:
        raise OSError(type(exc).__name__) from None
    finally:
        conn.close()


class Bitrix24Reader:
    """Bounded reader over the Bitrix24 REST API through an inbound webhook."""

    type = BITRIX

    def __init__(
        self,
        webhook_url: str,
        *,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_requests: int = MAX_REQUESTS,
    ) -> None:
        self.host, self._base = parse_bitrix_webhook(webhook_url)
        self._transport = transport or default_bitrix_transport
        self._sleep = sleep
        self._clock = clock
        self.max_requests = max_requests
        self.calls = 0

    @property
    def portal(self) -> str:
        return self.host

    def deal_url(self, deal_id: Any) -> str:
        return f"https://{self.host}/crm/deal/details/{deal_id}/"

    def _post(self, method: str, params: dict) -> dict:
        body = parse.urlencode(list(_flatten(params))).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        for attempt in range(2):
            if self.calls >= self.max_requests:
                raise CrmError("budget")
            _wait_turn(self.host, BITRIX_INTERVAL, self._sleep, self._clock)
            self.calls += 1
            try:
                response = self._transport("POST", self._base + method + ".json", headers, body)
            except OSError:
                if attempt == 0:
                    self._sleep(1)
                    continue
                raise CrmError("network") from None
            try:
                return self._read(response, attempt)
            except _Retry:
                self._sleep(1)
        raise CrmError("network")

    def _read(self, response: Response, attempt: int) -> dict:
        status = response.status
        if 300 <= status < 400:
            raise CrmError("redirect")
        if len(response.body) > MAX_BYTES:
            raise CrmError("limit")
        try:
            payload = json.loads(response.body) if response.body.strip() else {}
        except (ValueError, UnicodeError):
            payload = None
        if not isinstance(payload, dict):
            if status >= 500 and attempt == 0:
                raise _Retry()
            raise CrmError("network" if status >= 500 else "protocol")
        code = str(payload.get("error") or "").upper()
        text = str(payload.get("error_description") or "").lower()
        if code or status >= 400:
            if code == "QUERY_LIMIT_EXCEEDED" or status == 429:
                if attempt == 0:
                    raise _Retry()
                _pause(self.host, RATE_PAUSE_SECONDS, self._clock)
                raise CrmError("rate_limited")
            if status == 402 or "commercial" in text or "tariff" in text or code in {"PAYMENT_REQUIRED"}:
                raise CrmError("plan_closed")
            if status == 401 or code in {"INVALID_CREDENTIALS", "NO_AUTH_FOUND", "WRONG_AUTH_TYPE", "EXPIRED_TOKEN"}:
                raise CrmError("bad_key")
            if code in {"INSUFFICIENT_SCOPE", "ACCESS_DENIED"} or status == 403:
                raise CrmError("forbidden", code)
            if status >= 500 and attempt == 0:
                raise _Retry()
            raise CrmError("network" if status >= 500 else "protocol", code[:60])
        return scrub(payload)

    def call(self, method: str, params: Optional[dict] = None) -> dict:
        params = params or {}
        _validate_bitrix(method, params)
        return self._post(method, params)

    def collect(self, method: str, params: Optional[dict] = None, *, max_pages: int = 10) -> dict:
        """All pages of a list method up to ``max_pages``.

        Returns ``{"items", "total", "truncated"}``; ``truncated`` means more
        rows exist than were read.
        """
        if method not in BITRIX_LISTS:
            raise CrmError("not_allowed", method)
        params = copy.deepcopy(params or {})
        _validate_bitrix(method, params)
        cursor = params.pop("start", 0)
        seen: set[str] = set()
        items: list = []
        total: Optional[int] = None
        for _ in range(max_pages):
            if str(cursor) in seen:
                raise CrmError("protocol", "loop")
            seen.add(str(cursor))
            data = self.call(method, {**params, "start": cursor})
            result = data.get("result")
            nested = result if isinstance(result, dict) else {}
            if isinstance(result, dict):
                result = next((v for v in result.values() if isinstance(v, list)), None)
            if not isinstance(result, list):
                raise CrmError("protocol", "list")
            items.extend(result)
            raw_total = data.get("total", nested.get("total"))
            if raw_total is not None:
                try:
                    total = int(raw_total)
                except (TypeError, ValueError):
                    pass
            cursor = data.get("next", nested.get("next"))
            if cursor is None:
                return {"items": items, "total": total if total is not None else len(items), "truncated": False}
        return {"items": items, "total": total, "truncated": True}


class _Retry(Exception):
    pass


# ---------------------------------------------------------------- amoCRM


def parse_amo_domain(value: str) -> str:
    """Account host (``name.amocrm.ru``) from what the owner typed, or raise ``bad_url``."""
    text = (value or "").strip()
    if "://" in text:
        try:
            parts = parse.urlsplit(text)
            port = parts.port
        except ValueError:
            raise CrmError("bad_url") from None
        if parts.scheme != "https" or port not in (None, 443) or parts.username or parts.password:
            raise CrmError("bad_url")
        text = parts.hostname or ""
    host = text.strip().strip("/").lower()
    if not AMO_HOST.fullmatch(host):
        raise CrmError("bad_url")
    return host


class AmoTransport:
    """HTTPS to the account, over a local Unix socket when one is given.

    The certificate is always checked against the account host (SNI too), so
    the token stays inside TLS also on the socket route. Addresses are tried
    one by one with a short timeout, the last working one first: some hosts
    open TCP to part of amoCRM's addresses and hang in the handshake.
    """

    def __init__(self, domain: str, unix_socket: str = "") -> None:
        self.domain = domain
        self.unix_socket = unix_socket
        if unix_socket and not Path(unix_socket).is_absolute():
            raise CrmError("bad_url", "socket")
        self._conn: Optional[http.client.HTTPSConnection] = None
        self._live: Optional[str] = None

    def _addresses(self) -> list[str]:
        if self.unix_socket:
            return ["unix"]
        seen: list[str] = []
        for info in socket.getaddrinfo(self.domain, 443, proto=socket.IPPROTO_TCP):
            address = info[4][0]
            if address not in seen and is_global_address(address):
                seen.append(address)
        if not seen:
            raise CrmError("bad_url", "address")
        return seen

    def _connect(self, address: str) -> http.client.HTTPSConnection:
        context = ssl.create_default_context()
        conn = http.client.HTTPSConnection(self.domain, 443, timeout=ADDRESS_TIMEOUT, context=context)
        if self.unix_socket:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        else:
            sock = socket.create_connection((address, 443), timeout=ADDRESS_TIMEOUT)
        try:
            if self.unix_socket:
                sock.settimeout(ADDRESS_TIMEOUT)
                sock.connect(self.unix_socket)
            else:
                _require_global_peer(sock)
            conn.sock = context.wrap_socket(sock, server_hostname=self.domain)
        except BaseException:
            sock.close()
            raise
        return conn

    def __call__(self, method: str, url: str, headers: dict, body: Optional[bytes]) -> Response:
        parts = parse.urlsplit(url)
        endpoint = parts.path + (f"?{parts.query}" if parts.query else "")
        addresses = self._addresses()
        schedule = ([self._live] if self._live else []) + addresses * HANDSHAKE_ATTEMPTS
        last: Optional[Exception] = None
        for address in schedule:
            try:
                if self._conn is None or self._live != address:
                    self.close()
                    self._conn = self._connect(address)
                    self._live = address
                self._conn.request(method, endpoint, body=body, headers={**headers, "Host": self.domain})
                response = self._conn.getresponse()
                raw = response.read(MAX_BYTES + 1)
                if response.getheader("Connection", "").lower() == "close":
                    self.close()
                return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, raw)
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                last = exc
                self.close()
        raise OSError(type(last).__name__ if last else "unreachable")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
        self._conn, self._live = None, None


class AmoReader:
    """Bounded ``GET``-only reader over the amoCRM v4 API with a long-lived token."""

    type = AMOCRM

    def __init__(
        self,
        domain: str,
        token: str,
        *,
        unix_socket: str = "",
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_requests: int = MAX_REQUESTS,
    ) -> None:
        self.host = parse_amo_domain(domain)
        token = (token or "").strip()
        if not token or re.search(r"\s", token):
            raise CrmError("bad_key")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "korra21-amocrm-readonly/2",
        }
        self._owned_transport = transport is None
        self._transport = transport or AmoTransport(self.host, unix_socket)
        self._sleep = sleep
        self._clock = clock
        self.max_requests = max_requests
        self.calls = 0

    @property
    def portal(self) -> str:
        return self.host

    def deal_url(self, deal_id: Any) -> str:
        return f"https://{self.host}/leads/detail/{deal_id}"

    def close(self) -> None:
        if self._owned_transport:
            self._transport.close()  # type: ignore[union-attr]

    def get(self, path: str, params: Optional[Any] = None) -> Any:
        path = "/api/v4/" + path.strip("/")
        if not any(rule.fullmatch(path) for rule in _AMO_PATHS):
            raise CrmError("not_allowed", path)
        query = ""
        if params:
            pairs = params.items() if isinstance(params, dict) else params
            query = "?" + parse.urlencode(list(pairs), doseq=True)
        for attempt in range(2):
            if self.calls >= self.max_requests:
                raise CrmError("budget")
            _wait_turn(self.host, AMO_INTERVAL, self._sleep, self._clock)
            self.calls += 1
            try:
                response = self._transport("GET", f"https://{self.host}{path}{query}", self._headers, None)
            except OSError:
                raise CrmError("network") from None
            status = response.status
            if status == 204:
                return None
            if status == 401:
                raise CrmError("bad_key")
            if status == 402:
                raise CrmError("plan_closed")
            if status == 403:
                raise CrmError("forbidden")
            if status == 429:
                retry = response.headers.get("retry-after", "")
                _pause(self.host, float(retry) if retry.isdigit() else RATE_PAUSE_SECONDS, self._clock)
                raise CrmError("rate_limited")
            if 300 <= status < 400:
                raise CrmError("redirect")
            if status >= 500 and attempt == 0:
                self._sleep(1)
                continue
            if status >= 400:
                raise CrmError("network" if status >= 500 else "protocol", f"http_{status}")
            if len(response.body) > MAX_BYTES:
                raise CrmError("limit")
            try:
                data = json.loads(response.body) if response.body.strip() else None
            except (ValueError, UnicodeError):
                raise CrmError("protocol") from None
            return scrub(data)
        raise CrmError("network")

    def paged(self, path: str, params: Optional[Any], key: str, *, limit: int, page_size: int = 250,
              max_pages: int = 10) -> dict:
        """Up to ``limit`` rows of ``_embedded[key]``.

        ``read`` is how many rows were actually fetched (before the cut); ``complete``
        means the last page was short, so ``read`` is everything there is.
        ``truncated`` is true when rows exist that are not in ``items``.
        """
        pairs = list(params.items()) if isinstance(params, dict) else list(params or [])
        size = max(1, min(page_size, 250))
        items: list = []
        complete = False
        for page in range(1, max_pages + 1):
            data = self.get(path, pairs + [("limit", size), ("page", page)])
            if data is not None and not isinstance(data, dict):
                raise CrmError("protocol")
            batch = ((data or {}).get("_embedded") or {}).get(key) or []
            if not isinstance(batch, list):
                raise CrmError("protocol")
            items.extend(batch)
            if len(batch) < size:
                complete = True
                break
            if len(items) >= limit:
                break
        return {
            "items": items[:limit],
            "read": len(items),
            "complete": complete,
            "truncated": not complete or len(items) > limit,
        }
