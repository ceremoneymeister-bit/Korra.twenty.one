"""Small MAX HTTP client. Attachment IP pinning adapted from Marina's adapter.

No token in URLs, no redirect following, no retries of ambiguous message POSTs.
The extra CA is local to MAX connections; other hosts keep system trust only.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import mimetypes
import socket
import ssl
import threading
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

API_URL = "https://platform-api2.max.ru"
MAX_TEXT_LENGTH = 4000
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


class MaxError(RuntimeError):
    pass


class MaxAuthError(MaxError):
    pass


class MaxPollingError(MaxError):
    pass


class _RateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.next_request = self.next_message = 0.0

    async def wait(self, message):
        # Reserve slots across clients/threads/event loops (gateway + cron).
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next_request, self.next_message if message else 0)
            self.next_request = slot + 0.21
            if message:
                self.next_message = slot + 0.51
        await asyncio.sleep(slot - now)


@lru_cache(maxsize=128)
def _limiter(token_digest: bytes):
    return _RateLimiter()


def ssl_context(host: str) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if any(host == domain or host.endswith("." + domain)
           for domain in ("max.ru", "oneme.ru", "okcdn.ru")):
        context.load_verify_locations(Path(__file__).with_name("russian_trusted_root_ca.pem"))
    return context


def pinned_url(url: str) -> tuple[str, str, str]:
    """Resolve once, reject every non-public answer, retain TLS hostname/SNI."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise MaxError("Небезопасный адрес вложения MAX")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
        raise MaxError("Непубличный адрес вложения MAX")
    host = f"[{ips[0]}]" if ips[0].version == 6 else str(ips[0])
    port = f":{parsed.port}" if parsed.port else ""
    return (urlunsplit(("https", host + port, parsed.path, parsed.query, "")),
            parsed.hostname + port, parsed.hostname)


class MaxClient:
    def __init__(self, token: str):
        self._http = httpx.AsyncClient(
            base_url=API_URL, headers={"Authorization": token.strip()},
            verify=ssl_context("platform-api2.max.ru"),
            timeout=httpx.Timeout(20, connect=10), follow_redirects=False, trust_env=False,
        )
        self._limiter = _limiter(hashlib.sha256(token.strip().encode()).digest())

    async def close(self):
        await self._http.aclose()

    async def _pace(self, message=False):
        # <= 5 requests/s; <= 2 messages/s also satisfies MAX's per-chat limit.
        await self._limiter.wait(message)

    async def request(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(4):
            await self._pace(message=path == "/messages")
            try:
                response = await self._http.request(method, path, **kwargs)
            except httpx.TransportError:
                if method == "GET" and attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise MaxError("Нет подтверждения от MAX; отправка автоматически не повторяется") from None
            if response.status_code in (401, 403):
                raise MaxAuthError("Токен не принят")
            if path == "/updates" and response.status_code == 405:
                raise MaxPollingError("Опрос MAX недоступен. Убедитесь, что у бота нет активной webhook-подписки.")
            try:
                data = response.json()
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            # These errors explicitly reject the send, so retrying cannot
            # duplicate a message. A POST 5xx or broken connection is ambiguous.
            rejected = response.status_code == 429 or (
                path == "/messages" and response.status_code == 400
                and data.get("code") == "attachment.not.ready"
            )
            if attempt < 3 and (rejected or (method == "GET" and response.status_code >= 500)):
                try:
                    delay = max(2 ** attempt, float(response.headers.get("Retry-After", "0")))
                except ValueError:
                    delay = 2 ** attempt
                await asyncio.sleep(delay)
                continue
            if not 200 <= response.status_code < 300:
                raise MaxError(f"MAX: HTTP {response.status_code}. Отправка не подтверждена")
            if not data:
                raise MaxError("Некорректный ответ MAX")
            return data
        raise AssertionError("unreachable")

    async def me(self) -> dict:
        data = await self.request("GET", "/me")
        if data.get("is_bot") is not True or not isinstance(data.get("user_id"), int):
            raise MaxError("MAX не подтвердил учётную запись бота")
        return data

    async def send(self, chat_id, text, *, attachment=None, reply_to=None) -> str:
        body = {"text": text or None}
        if attachment:
            body["attachments"] = [attachment]
        if reply_to:
            body["link"] = {"type": "reply", "mid": str(reply_to)}
        data = await self.request("POST", "/messages", params={"chat_id": int(chat_id)}, json=body)
        mid = ((data.get("message") or {}).get("body") or {}).get("mid")
        if not mid:
            raise MaxError("MAX не вернул ID сообщения; отправка автоматически не повторяется")
        return str(mid)

    async def download(self, attachment: dict) -> tuple[bytes, str, str]:
        kind = attachment["type"]
        payload = attachment.get("payload") or {}
        url = payload.get("url", "")
        if kind == "video":
            data = await self.request("GET", "/videos/" + quote(str(payload.get("token", "")), safe=""))
            urls = data.get("urls") or {}
            url = next((urls.get(key) for key in ("mp4_720", "mp4_480", "mp4_360", "mp4_240", "mp4_144", "mp4_1080")
                        if urls.get(key)), "")
        request_url, host, sni = await asyncio.to_thread(pinned_url, url)
        async with httpx.AsyncClient(verify=ssl_context(sni), timeout=30,
                                     follow_redirects=False, trust_env=False) as http:
            async with http.stream("GET", request_url, headers={"Host": host},
                                   extensions={"sni_hostname": sni}) as response:
                if response.status_code != 200:
                    raise MaxError(f"Вложение MAX: HTTP {response.status_code}")
                mime = response.headers.get("content-type", "application/octet-stream").split(";")[0].lower()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_ATTACHMENT_BYTES:
                        raise MaxError("Вложение MAX превышает 25 МиБ")
        if kind in {"image", "audio", "video"} and not mime.startswith(kind + "/"):
            raise MaxError("Тип вложения MAX не совпадает с содержимым")
        filename = Path(str(attachment.get("filename") or kind)).name
        return bytes(content), mime, filename

    async def upload(self, path: str, kind: str, filename=None) -> dict:
        file = Path(path)
        if not file.is_file() or file.stat().st_size > MAX_ATTACHMENT_BYTES:
            raise MaxError("Файл не найден или превышает 25 МиБ")
        info = await self.request("POST", "/uploads", params={"type": kind})
        url, host, sni = await asyncio.to_thread(pinned_url, info.get("url", ""))
        name = Path(filename).name if filename else file.name
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        await self._pace()
        async with httpx.AsyncClient(verify=ssl_context(sni), timeout=60,
                                     follow_redirects=False, trust_env=False) as http:
            with file.open("rb") as content:
                response = await http.post(url, files={"data": (name, content, mime)},
                                           headers={"Host": host}, extensions={"sni_hostname": sni})
        if response.status_code not in (200, 201):
            raise MaxError(f"Загрузка MAX: HTTP {response.status_code}")
        # Audio/video tokens come from /uploads; files/images from the upload.
        token = info.get("token")
        if not token:
            data = response.json()
            token = data.get("token")
            if kind == "image" and not token:
                token = next((photo.get("token") for photo in (data.get("photos") or {}).values()
                              if isinstance(photo, dict) and photo.get("token")), None)
        if not token:
            raise MaxError("MAX не вернул токен вложения")
        return {"type": kind, "payload": {"token": token}}


async def test_connection(env: dict) -> dict:
    token = env.get("MAX_BOT_TOKEN", "").strip()
    if not token:
        return {"ok": False, "state": "unconfigured", "message": "Укажите токен бота MAX"}
    client = MaxClient(token)
    try:
        bot = await asyncio.wait_for(client.me(), timeout=20)
        name = bot.get("name") or bot.get("first_name") or bot.get("username") or str(bot["user_id"])
        return {"ok": True, "state": "connected", "message": f"Бот {name} подключён"}
    except MaxAuthError:
        return {"ok": False, "state": "error", "message": "Токен не принят"}
    except (MaxError, httpx.HTTPError, asyncio.TimeoutError):
        return {"ok": False, "state": "error", "message": "Не удалось связаться с MAX. Проверьте сеть и повторите проверку."}
    finally:
        await client.close()
