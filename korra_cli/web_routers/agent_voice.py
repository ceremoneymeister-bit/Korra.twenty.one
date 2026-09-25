"""Per-agent voice settings and explicit, bounded synthesis requests."""
from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from threading import BoundedSemaphore
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from korra_cli import agent_voice
from korra_cli.web_deps import LateState, late

router = APIRouter()
scope = late("_config_profile_scope")
mutation_lock = LateState("_CONFIG_MUTATION_LOCK")
speech_slots = BoundedSemaphore(2)


class VoiceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    provider: Literal["elevenlabs", "compatible"] = "elevenlabs"
    voice: str = Field(default="", max_length=200)
    model: str = Field(default="eleven_multilingual_v2", max_length=200)
    base_url: str = Field(default="", max_length=2000)
    speed: float = Field(default=1, ge=0.7, le=1.2)
    web_mode: Literal["manual", "auto"] = "manual"
    telegram_mode: Literal["off", "voice_only", "all"] = "off"
    api_key: str | None = Field(default=None, max_length=4096, repr=False)
    clear_key: bool = False


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


def read_settings():
    from korra_cli.config import load_config
    cfg = load_config()
    value = agent_voice.settings(cfg) or {}
    provider = value.get("provider", "elevenlabs")
    section = (cfg.get("tts") or {}).get(provider) or {}
    return {
        "enabled": bool(value.get("enabled", False)), "provider": provider,
        "voice": section.get("voice_id" if provider == "elevenlabs" else "voice", ""),
        "model": section.get("model_id" if provider == "elevenlabs" else "model", "eleven_multilingual_v2" if provider == "elevenlabs" else ""),
        "base_url": section.get("base_url", "") if provider == "compatible" else "",
        "speed": section.get("speed", 1), "web_mode": value.get("web_mode", "manual"),
        "telegram_mode": value.get("telegram_mode", "off"),
        "has_key": bool(agent_voice.profile_key(provider)),
    }


@router.get("/api/profiles/{name}/voice")
async def get_voice(name: str):
    def read():
        with scope(name):
            return read_settings()
    return await asyncio.to_thread(read)


@router.put("/api/profiles/{name}/voice")
async def put_voice(name: str, body: VoiceUpdate):
    from korra_cli.config import load_config, remove_env_value, save_config, save_env_value
    data = body.model_dump(exclude={"api_key", "clear_key"})
    data["voice"] = body.voice.strip()
    data["model"] = body.model.strip()
    if body.provider == "compatible" and body.base_url:
        try:
            data["base_url"] = agent_voice.validate_endpoint(body.base_url)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    if body.api_key is not None and (not body.api_key.isascii() or "\n" in body.api_key or "\r" in body.api_key):
        raise HTTPException(400, "Ключ содержит недопустимые символы")

    def write():
        with scope(name), mutation_lock:
            key = "" if body.clear_key else (body.api_key or agent_voice.profile_key(body.provider)).strip()
            if body.enabled and (not data["voice"] or not data["model"]):
                raise HTTPException(400, "Укажите голос и модель")
            if body.enabled and body.provider == "elevenlabs" and not key:
                raise HTTPException(400, "Добавьте свой API-ключ ElevenLabs")
            if body.enabled and body.provider == "compatible" and not data["base_url"]:
                raise HTTPException(400, "Укажите адрес сервиса озвучки")
            cfg = load_config()
            cfg.setdefault("voice", {})["agent_voice"] = {
                k: data[k] for k in ("enabled", "provider", "web_mode", "telegram_mode")
            }
            tts = cfg.setdefault("tts", {})
            # Saving a disabled draft must not replace an existing speech setup.
            if body.enabled:
                tts["provider"] = body.provider
            section = tts.setdefault(body.provider, {})
            section.update({
                "voice_id" if body.provider == "elevenlabs" else "voice": data["voice"],
                "model_id" if body.provider == "elevenlabs" else "model": data["model"],
                "speed": body.speed,
            })
            if body.provider == "compatible":
                section["base_url"] = data["base_url"]
            if body.clear_key:
                remove_env_value(agent_voice.VOICE_KEYS[body.provider], update_process=False)
            elif body.api_key:
                save_env_value(agent_voice.VOICE_KEYS[body.provider], key, update_process=False)
            save_config(cfg)
            return read_settings()
    return await asyncio.to_thread(write)


@router.get("/api/profiles/{name}/voice/voices")
async def list_voices(name: str):
    import httpx

    def fetch():
        with scope(name):
            value = read_settings()
            if value["provider"] != "elevenlabs":
                return {"voices": [], "manual": True}
            key = agent_voice.profile_key("elevenlabs")
            if not key:
                raise HTTPException(400, "Сначала сохраните API-ключ ElevenLabs")
            try:
                reply = httpx.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=15)
                reply.raise_for_status()
                rows = reply.json().get("voices", [])
                return {"voices": [{"id": row["voice_id"], "name": row.get("name") or row["voice_id"]} for row in rows if row.get("voice_id")]}
            except Exception:
                raise HTTPException(502, "Не удалось загрузить голоса. Проверьте ключ и доступность ElevenLabs.") from None
    return await asyncio.to_thread(fetch)


@router.post("/api/profiles/{name}/voice/speak")
async def speak(name: str, body: SpeechRequest):
    def generate_scoped():
        with scope(name):
            value = read_settings()
            if not value["enabled"]:
                raise HTTPException(409, "Сначала включите и сохраните голос агента")
            from tools.tts_tool import text_to_speech_tool
            from gateway.platforms.base import get_audio_cache_dir
            # Own temporary directory: cleanup every chunk even on partial failure.
            # The installed image permits tool output only under DATA, not /tmp.
            with tempfile.TemporaryDirectory(prefix="korra-voice-", dir=get_audio_cache_dir()) as directory:
                result = json.loads(text_to_speech_tool(body.text, output_path=str(Path(directory) / "speech.mp3"), provider=value["provider"]))
                if not result.get("success"):
                    raise HTTPException(502, "Не удалось озвучить ответ. Проверьте сервис, ключ, модель и голос.")
                paths = result.get("file_paths") or [result.get("file_path")]
                clips = []
                size = 0
                for item in paths:
                    path = Path(item).resolve()
                    if not path.is_relative_to(Path(directory).resolve()):
                        raise HTTPException(502, "Сервис вернул некорректный файл")
                    size += path.stat().st_size
                    if size > 25 * 1024 * 1024:
                        raise HTTPException(502, "Аудио превышает допустимый размер")
                    mime = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg"}.get(path.suffix, "audio/mpeg")
                    clips.append(f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii"))
                return {"clips": clips}
    def generate():
        if not speech_slots.acquire(blocking=False):
            raise HTTPException(429, "Сейчас готовятся другие голосовые ответы. Попробуйте чуть позже.")
        try:
            return generate_scoped()
        finally:
            speech_slots.release()
    try:
        return await asyncio.to_thread(generate)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Сервис озвучки недоступен. Текст ответа сохранён.") from None
