"""MAX long-polling platform, using the gateway's admission and owner policy.

Message field mapping follows Marina Biryukova's max-platform/normalize.py.
No installation-specific identity, webhook or outbound authorization layer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import OrderedDict
from datetime import datetime, timezone

import httpx

from agent.secret_scope import UnscopedSecretError, get_secret
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult, cache_media_bytes
from korra_constants import get_hermes_home
from utils import atomic_json_write

from .client import MAX_TEXT_LENGTH, MaxAuthError, MaxClient, MaxError, MaxPollingError, test_connection

logger = logging.getLogger(__name__)
MEDIA_TYPES = {"image": MessageType.PHOTO, "audio": MessageType.VOICE,
               "video": MessageType.VIDEO, "file": MessageType.DOCUMENT}


def _token(config=None):
    try:
        token = get_secret("MAX_BOT_TOKEN", "")
    except UnscopedSecretError:
        token = os.getenv("MAX_BOT_TOKEN", "")
    return (token or getattr(config, "token", "") or "").strip()


class MaxAdapter(BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = MAX_TEXT_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("max"))
        self._client = None
        self._poll_task = None
        self._bot_id = None
        self._marker = None
        self._marker_path = None
        self._seen = OrderedDict()

    async def connect(self, *, is_reconnect=False):
        await self.disconnect()
        token = _token(self.config)
        if not token:
            self._set_fatal_error("config_missing", "Укажите токен бота MAX", retryable=False)
            return False
        self._client = MaxClient(token)
        try:
            bot = await self._client.me()
            self._bot_id = str(bot["user_id"])
            if not self._acquire_platform_lock("max", self._bot_id, "бот MAX"):
                await self.disconnect()
                return False
            self._marker_path = get_hermes_home() / "gateway" / f"max-{self._bot_id}-marker.json"
            try:
                self._marker = json.loads(self._marker_path.read_text(encoding="utf-8"))["marker"]
            except (OSError, ValueError, KeyError, TypeError):
                self._marker = None
            self._mark_connected()
            self._poll_task = asyncio.create_task(self._poll())
            self._wire_plugin_handlers(None)
            return True
        except (MaxError, httpx.HTTPError, OSError) as exc:
            await self.disconnect()
            self._set_fatal_error("max_connect", str(exc) if isinstance(exc, MaxError)
                                  else "Не удалось подключиться к MAX", retryable=not isinstance(exc, MaxAuthError))
            return False

    async def disconnect(self):
        if self._poll_task:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
            self._poll_task = None
        if self._client:
            await self._client.close()
            self._client = None
        self._release_platform_lock()
        self._mark_disconnected()

    async def _poll(self):
        while True:
            try:
                params = {"timeout": 90, "types": "message_created"}
                if self._marker is not None:
                    params["marker"] = self._marker
                data = await self._client.request("GET", "/updates", params=params, timeout=httpx.Timeout(100, connect=10))
                updates = data.get("updates")
                if not isinstance(updates, list):
                    raise MaxError("Некорректный ответ опроса MAX")
                for update in updates:
                    try:
                        await self._receive(update)
                    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                        logger.warning("MAX: пропущено некорректное событие")
                marker = data.get("marker")
                if type(marker) is int and marker != self._marker:
                    await asyncio.to_thread(atomic_json_write, self._marker_path, {"marker": marker}, mode=0o600)
                    self._marker = marker
            except (MaxAuthError, MaxPollingError) as exc:
                self._set_fatal_error("max_poll", str(exc), retryable=False)
                await self._notify_fatal_error()
                return
            except (MaxError, httpx.HTTPError, OSError):
                logger.warning("MAX: опрос временно недоступен; повтор через 5 секунд")
                await asyncio.sleep(5)

    def _parse(self, update):
        if update.get("update_type") != "message_created":
            return None
        message = update["message"]
        sender, recipient, body = message["sender"], message["recipient"], message["body"]
        if (type(sender.get("user_id")) is not int or type(recipient.get("chat_id")) is not int
                or not isinstance(body.get("mid"), str) or not body["mid"]):
            raise ValueError("Missing MAX message identity")
        mid, uid, chat_id = str(body["mid"]), str(sender["user_id"]), str(recipient["chat_id"])
        if sender.get("is_bot") is True or uid == self._bot_id:
            return None
        chat_type = {"dialog": "dm", "chat": "group", "channel": "channel"}.get(recipient.get("chat_type"), "group")
        name = " ".join(str(sender.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
        source = self.build_source(chat_id=chat_id, chat_type=chat_type, user_id=uid,
                                   user_name=name or sender.get("name") or sender.get("username") or uid,
                                   message_id=mid)
        link = message.get("link") or {}
        return MessageEvent(
            text=body.get("text") or "", message_type=MessageType.TEXT,
            source=source, user_id=uid, user_name=source.user_name,
            message_id=mid, raw_message=update,
            timestamp=datetime.fromtimestamp(float(message.get("timestamp", update.get("timestamp"))) / 1000, tz=timezone.utc),
            reply_to_message_id=(link.get("message") or {}).get("mid") if link.get("type") == "reply" else None,
        )

    async def _receive(self, update):
        event = self._parse(update)
        if event is None or event.message_id in self._seen:
            return
        # Gateway still receives denied senders for its normal pairing/denial
        # UX, but their URLs never trigger attachment downloads.
        allowed = self._is_sender_authorized(event.user_id, event.source.chat_type, event.source.chat_id)
        attachments = update["message"]["body"].get("attachments") or []
        for attachment in attachments:
            kind = attachment.get("type")
            if kind not in MEDIA_TYPES:
                event.text += f"\n[Неподдерживаемое вложение MAX: {str(kind)[:40]}]"
                continue
            if allowed is not True:
                event.text += "\n[Вложение MAX]"
                continue
            try:
                data, mime, name = await self._client.download(attachment)
                cached = await asyncio.to_thread(cache_media_bytes, data, filename=name,
                                                 mime_type=mime, default_kind="document" if kind == "file" else kind)
                if cached is None:
                    raise MaxError("Изображение не распознано")
                event.media_urls.append(cached.path)
                event.media_types.append(cached.media_type)
                if len(event.media_urls) == 1:
                    event.message_type = MEDIA_TYPES[kind]
                event.text += "\n" + cached.context_note()
            except (MaxError, httpx.HTTPError, OSError, ValueError):
                event.text += "\n[Не удалось загрузить вложение MAX: недоступно, небезопасно или больше 25 МиБ]"
        if not event.text.strip() and not event.media_urls:
            return
        await self.handle_message(event)
        self._seen[event.message_id] = None
        if len(self._seen) > 2048:
            self._seen.popitem(last=False)

    async def _send(self, chat_id, content, *, attachment=None, reply_to=None):
        ids = []
        try:
            if self._client is None:
                raise MaxError("MAX не подключён")
            # Plain text preserves every character, including code and URLs.
            chunks = [content[i:i + MAX_TEXT_LENGTH] for i in range(0, len(content), MAX_TEXT_LENGTH)] or [""]
            for index, chunk in enumerate(chunks):
                ids.append(await self._client.send(chat_id, chunk, attachment=attachment if index == 0 else None,
                                                   reply_to=reply_to if index == 0 else None))
            return SendResult(success=True, message_id=ids[-1], continuation_message_ids=tuple(ids[:-1]))
        except (MaxError, httpx.HTTPError, OSError, ValueError, TypeError) as exc:
            error = str(exc) if isinstance(exc, MaxError) else "Ошибка отправки MAX; автоматического повтора нет"
            return SendResult(success=False, error=error, message_id=ids[-1] if ids else None,
                              continuation_message_ids=tuple(ids), retryable=False)

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return await self._send(chat_id, content, reply_to=reply_to)

    async def _send_with_retry(self, chat_id, content, reply_to=None, metadata=None, **kwargs):
        # The base plain-text fallback would resend an ambiguous POST or a
        # partially delivered multipart reply. MAX already sends plain text.
        return await self.send(chat_id, content, reply_to=reply_to, metadata=metadata)

    async def _send_file(self, chat_id, path, kind, caption=None, reply_to=None, metadata=None, file_name=None):
        try:
            if self._client is None:
                raise MaxError("MAX не подключён")
            attachment = await self._client.upload(path, kind, filename=file_name)
        except (MaxError, httpx.HTTPError, OSError, ValueError, TypeError):
            return SendResult(success=False, error="Не удалось загрузить файл в MAX (предел 25 МиБ)")
        return await self._send(chat_id, caption or "", attachment=attachment, reply_to=reply_to)

    async def send_document(self, chat_id, file_path, caption=None, **kwargs):
        return await self._send_file(chat_id, file_path, "file", caption, **kwargs)

    async def send_image_file(self, chat_id, image_path, caption=None, **kwargs):
        return await self._send_file(chat_id, image_path, "image", caption, **kwargs)

    async def send_voice(self, chat_id, audio_path, caption=None, **kwargs):
        return await self._send_file(chat_id, audio_path, "audio", caption, **kwargs)

    async def send_video(self, chat_id, video_path, caption=None, **kwargs):
        return await self._send_file(chat_id, video_path, "video", caption, **kwargs)

    async def send_image(self, chat_id, image_url, caption=None, **kwargs):
        try:
            data, mime, name = await self._client.download({"type": "image", "payload": {"url": image_url}})
            cached = await asyncio.to_thread(cache_media_bytes, data, filename=name, mime_type=mime, default_kind="image")
            if cached is None:
                raise MaxError("Изображение не распознано")
            return await self.send_image_file(chat_id, cached.path, caption, **kwargs)
        except (MaxError, httpx.HTTPError, OSError, ValueError):
            return SendResult(success=False, error="Не удалось загрузить изображение MAX")

    async def get_chat_info(self, chat_id):
        return {"name": str(chat_id), "type": "group", "chat_id": str(chat_id)}


async def _standalone_send(pconfig, chat_id, message, *, thread_id=None, media_files=None, force_document=False):
    from tools.send_message_tool import _send_live_adapter_media

    adapter = MaxAdapter(pconfig)
    adapter._client = MaxClient(_token(pconfig))
    try:
        if media_files:
            return await _send_live_adapter_media(adapter, chat_id, message, media_files,
                                                  force_document=force_document)
        result = await adapter.send(chat_id, message)
        return {"success": True, "message_id": result.message_id} if result.success else {"error": result.error}
    finally:
        await adapter._client.close()


def register(ctx):
    ctx.register_platform(
        name="max", label="MAX (Макс)", adapter_factory=MaxAdapter,
        check_fn=lambda: True, is_connected=lambda cfg: bool(_token(cfg)),
        required_env=["MAX_BOT_TOKEN"], allowed_users_env="MAX_ALLOWED_USERS",
        max_message_length=MAX_TEXT_LENGTH, standalone_sender_fn=_standalone_send,
        test_connection_fn=test_connection,
    )
