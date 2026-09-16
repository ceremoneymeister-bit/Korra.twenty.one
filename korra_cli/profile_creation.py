"""Recoverable publication of a fully prepared new profile, never an update."""
import hashlib
import json
import logging
import re
import shutil

from korra_cli import profiles
from utils import atomic_write_text

_log = logging.getLogger(__name__)


class ProfileCreateConflict(ValueError):
    """An operation or name was already used for a different profile."""


def publish_profile(key, name, request, populate, *,
                    namespace=".profile-requests", receipt_name=".profile-create.json"):
    profiles.validate_profile_name(name)
    if name == "default":
        raise ValueError("Главного агента нельзя заменить созданием профиля.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key or ""):
        raise ValueError("Нужен идентификатор операции создания агента.")
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    root = profiles._get_profiles_root()
    target = profiles.get_profile_dir(name)

    with profiles.profile_creation_lock():
        operations = root / namespace
        operation = operations / key_hash
        if root.is_symlink() or operations.is_symlink() or operation.is_symlink() or target.is_symlink():
            raise ProfileCreateConflict("Небезопасный путь профиля. Выберите другое имя.")
        operation.mkdir(parents=True, exist_ok=True, mode=0o700)
        record_path = operation / "request.json"
        record = {"fingerprint": fingerprint, "name": name}
        if record_path.exists():
            if json.loads(record_path.read_text(encoding="utf-8")) != record:
                raise ProfileCreateConflict("Эта операция уже использована с другими параметрами.")
        else:
            atomic_write_text(record_path, json.dumps(record), create_mode=0o600)
        receipt_path = target / receipt_name
        if target.exists():
            if receipt_path.is_file() and not receipt_path.is_symlink():
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if receipt.get("request") == key_hash and receipt.get("fingerprint") == fingerprint:
                    profiles.clear_named_profile_deleted(target)
                    atomic_write_text(operation / "completed", name, create_mode=0o600)
                    _activate(name)
                    return receipt["response"]
            raise ProfileCreateConflict("Агент с таким системным именем уже существует. Выберите другое имя.")
        # A completed request must not recreate a deliberately deleted agent.
        if (operation / "completed").exists():
            raise ProfileCreateConflict("Агент этой операции удалён или переименован. Начните новое добавление.")
        stage = operation / "stage"
        if stage.is_symlink():
            raise ProfileCreateConflict("Небезопасный временный каталог агента.")
        if stage.exists():
            shutil.rmtree(stage)  # only this recorded operation's unpublished work
        try:
            response, metadata = populate(stage)
            receipt = {"request": key_hash, "fingerprint": fingerprint, "response": response, **metadata}
            atomic_write_text(stage / receipt_name, json.dumps(receipt), create_mode=0o600)
            # Ordinary creates take the same lock. Existing profiles are never
            # passed to the distribution copier; this rename publishes a whole
            # role + skills + independent configuration on the same filesystem.
            stage.rename(target)
            profiles.clear_named_profile_deleted(target)
            atomic_write_text(operation / "completed", name, create_mode=0o600)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        _activate(name)
        return response


def _activate(name: str) -> None:
    # Registration is retryable and cannot turn a committed create into a
    # destructive retry. No model call, Telegram startup or image generation.
    profiles._maybe_register_gateway_service(name)
    try:
        if not profiles.check_alias_collision(name):
            profiles.create_wrapper_script(name)
    except Exception:
        _log.warning("Agent %s saved, CLI alias not installed", name, exc_info=True)
