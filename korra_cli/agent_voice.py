"""Owner-configured voices, scoped to the current agent home.

The product UI supplies a voice, not a model tool argument. Credentials are
read only from this profile's .env; another agent's process env is never used.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlsplit

from korra_constants import get_hermes_home

VOICE_KEYS = {"elevenlabs": "KORRA_VOICE_ELEVENLABS_KEY", "compatible": "KORRA_VOICE_HTTP_KEY"}


def settings(config=None):
    if config is None:
        from korra_cli.config import load_config
        config = load_config()
    value = (config.get("voice") or {}).get("agent_voice")
    return value if isinstance(value, dict) else None


def profile_key(provider):
    from agent.secret_scope import load_env_file
    key = VOICE_KEYS.get(provider)
    return str(load_env_file(get_hermes_home() / ".env").get(key, "")).strip() if key else ""


def telegram_mode():
    """None retains legacy /voice behavior; explicit opt-out stays off."""
    value = settings()
    if value is None:
        return None
    if not value.get("enabled"):
        return "off"
    return value.get("telegram_mode", "off")


def validate_endpoint(value):
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError
        _ = parsed.port
    except ValueError:
        raise ValueError("Укажите HTTP(S)-адрес API без пароля, параметров и фрагмента") from None
    return value


def generate_compatible(text, output_path, tts_config):
    """OpenAI-shaped speech API. No credential lookup or fallback to OpenAI."""
    section = tts_config.get("compatible") or {}
    endpoint = validate_endpoint(str(section.get("base_url") or ""))
    voice = str(section.get("voice") or "").strip()
    model = str(section.get("model") or "").strip()
    if not voice or not model:
        raise ValueError("Укажите модель и голос сервиса озвучки")
    key = profile_key("compatible")
    headers = {"Accept": "audio/*"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {"input": text, "model": model, "voice": voice, "response_format": "mp3"}
    speed = float(section.get("speed", 1))
    if speed != 1:
        payload["speed"] = speed
    return _request_audio(endpoint + "/audio/speech", payload, headers, output_path)


def generate_elevenlabs(text, output_path, tts_config):
    key = profile_key("elevenlabs")
    if not key:
        raise ValueError("Добавьте свой API-ключ ElevenLabs")
    section = tts_config.get("elevenlabs") or {}
    voice = str(section.get("voice_id") or "").strip()
    if not voice:
        raise ValueError("Выберите голос ElevenLabs")
    payload = {"text": text, "model_id": section.get("model_id", "eleven_multilingual_v2"),
               "voice_settings": {"speed": float(section.get("speed", 1))}}
    fmt = "opus_48000_64" if output_path.endswith(".ogg") else "mp3_44100_128"
    return _request_audio(
        "https://api.elevenlabs.io/v1/text-to-speech/" + quote(voice, safe="") + "/stream",
        payload, {"xi-api-key": key, "Accept": "audio/*"}, output_path,
        params={"output_format": fmt},
    )


def _request_audio(url, payload, headers, output_path, params=None):
    import httpx

    target = Path(output_path)
    size = 0
    try:
        # A redirect must never forward a credential to another service.
        with httpx.Client(timeout=90, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", url, json=payload, headers=headers, params=params) as reply:
                if reply.status_code != 200:
                    raise ValueError(f"Сервис озвучки вернул HTTP {reply.status_code}")
                if "json" in reply.headers.get("content-type", "").lower():
                    raise ValueError("Сервис вернул JSON вместо аудио")
                with target.open("wb") as stream:
                    for chunk in reply.iter_bytes():
                        size += len(chunk)
                        if size > 25 * 1024 * 1024:
                            raise ValueError("Аудио превышает допустимый размер")
                        stream.write(chunk)
        if not size:
            raise ValueError("Сервис вернул пустое аудио")
        return str(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
