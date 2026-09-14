"""First-party agent catalogue and recoverable, isolated profile installation.

No remote sources, embedded installers or updates of existing personas. The
receipt belongs to this installation (the cabinet selects the tenant engine).
Only creation uses it: a replay never recopies a user's modified template.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Callable

from korra_cli import profiles
from korra_cli.config import read_user_config_raw
from korra_cli.profile_distribution import _copy_dist_payload, read_manifest
from korra_cli.web_models import ProfileCreate
from utils import atomic_write_text

_log = logging.getLogger(__name__)
_PACKAGES = Path(__file__).parent / "data" / "agent_templates"
_CATALOGUE = {
    "korra.designer": {
        "directory": "designer",
        "owned_paths": {"SOUL.md", "distribution.yaml", "skills/visual-design"},
        "name": "Дизайнер",
        "description": "Универсальный дизайнер: презентации, визуалы, сторис, карусели и работа по референсам.",
        "requirements": [
            "Для общения нужна чат-модель; Claude или DeepSeek тоже подходят. Генератор подключается отдельно.",
            "Для генерации подключите поддерживаемый подписочный вход GPT или API-сервис; для GPT Image 2.5 нужен именно этот генератор. Если подключения нет, Дизайнер поможет с настройкой. Добавление агента не запускает генерацию.",
            "Для презентаций нужен доступный рендер и экспорт; агент проверит их перед работой.",
        ],
    },
}


class TemplateConflict(ValueError):
    """The request conflicts with a previous operation or an existing agent."""


def _template(template_id: str):
    entry = _CATALOGUE.get(template_id)
    if entry is None:
        raise ValueError("Такого готового агента нет в этой версии Korra.")
    source = _PACKAGES / entry["directory"]
    manifest = read_manifest(source)
    if manifest is None or not manifest.version:
        raise RuntimeError("Пакет готового агента повреждён. Требуется восстановление установки.")
    # Bundled data is reviewed, but fail closed on incomplete/tampered images.
    allowed = entry["owned_paths"]
    if set(manifest.distribution_owned) != allowed:
        raise RuntimeError("Некорректный состав пакета готового агента.")
    for relative in allowed:
        path = source / relative
        if not path.exists() or path.is_symlink():
            raise RuntimeError("Неполный пакет готового агента.")
    if any(p.is_symlink() for p in source.rglob("*")):
        raise RuntimeError("Ссылки в пакете готового агента не поддерживаются.")
    return entry, source, manifest


def list_agent_templates() -> list[dict]:
    result = []
    for template_id in _CATALOGUE:
        entry, _, manifest = _template(template_id)
        result.append({
            "id": template_id, "version": manifest.version,
            "name": entry["name"], "description": entry["description"],
            "requirements": entry["requirements"],
        })
    return result


def create_template_profile(
    body: ProfileCreate, *, write_model: Callable[[Path, str, str], None],
) -> dict:
    """Prepare privately, publish once, then register through the native path."""
    entry, source, manifest = _template(body.template_id or "")
    if body.template_version != manifest.version:
        raise TemplateConflict("Версия готового агента изменилась. Обновите каталог.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", body.idempotency_key or ""):
        raise ValueError("Для добавления готового агента нужен идентификатор операции.")
    if (body.clone_from or body.clone_from_default or body.clone_all or body.no_skills
            or body.soul is not None or body.mcp_servers or body.keep_skills or body.hub_skills):
        raise ValueError("Готовый агент создаётся отдельно: копирование и замена его пакета здесь не поддерживаются.")
    name = profiles.normalize_profile_name(body.name)
    profiles.validate_profile_name(name)
    if name == "default":
        raise ValueError("Главный агент не может быть заменён готовым агентом.")
    label = (body.display_name or entry["name"]).strip()
    if len(label) > 64:
        raise ValueError("Имя агента не должно быть длиннее 64 символов.")
    provider, model = (body.provider or "").strip(), (body.model or "").strip()
    if bool(provider) != bool(model):
        raise ValueError("Провайдер и модель должны быть выбраны вместе.")
    request = {
        "name": name, "display_name": label,
        "template_id": body.template_id, "template_version": body.template_version,
        "provider": provider, "model": model,
        "description": body.description or entry["description"],
    }
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    key_hash = hashlib.sha256(body.idempotency_key.encode()).hexdigest()
    root = profiles._get_profiles_root()
    target = profiles.get_profile_dir(name)

    with profiles.profile_creation_lock():
        operations = root / ".template-requests"
        operation = operations / key_hash
        if root.is_symlink() or operations.is_symlink() or operation.is_symlink() or target.is_symlink():
            raise TemplateConflict("Небезопасный путь профиля. Выберите другое имя.")
        operation.mkdir(parents=True, exist_ok=True, mode=0o700)
        record_path = operation / "request.json"
        record = {"fingerprint": fingerprint, "name": name}
        if record_path.exists():
            if json.loads(record_path.read_text(encoding="utf-8")) != record:
                raise TemplateConflict("Эта операция уже использована с другими параметрами.")
        else:
            atomic_write_text(record_path, json.dumps(record), create_mode=0o600)
        receipt_path = target / ".agent-template.json"
        if target.exists():
            if receipt_path.is_file() and not receipt_path.is_symlink():
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if receipt.get("request") == key_hash and receipt.get("fingerprint") == fingerprint:
                    profiles.clear_named_profile_deleted(target)
                    atomic_write_text(operation / "completed", name, create_mode=0o600)
                    _activate(name)
                    return receipt["response"]
            raise TemplateConflict("Агент с таким системным именем уже существует. Выберите другое имя.")
        # A completed request must not recreate a deliberately deleted agent.
        if (operation / "completed").exists():
            raise TemplateConflict("Агент этой операции удалён или переименован. Начните новое добавление.")
        stage = operation / "stage"
        if stage.is_symlink():
            raise TemplateConflict("Небезопасный временный каталог агента.")
        if stage.exists():
            shutil.rmtree(stage)  # only this recorded operation's unpublished work
        try:
            profiles.create_profile(
                name, display_name=label, description=request["description"],
                soul=(source / "SOUL.md").read_text(encoding="utf-8"),
                _staging_dir=stage,
            )
            if profiles.seed_profile_skills(stage, quiet=True) is None:
                raise RuntimeError("Не удалось подготовить навыки агента. Повторите добавление.")
            _copy_dist_payload(source, stage, manifest, preserve_config=True)
            seeded = []
            if provider and model:
                write_model(stage, provider, model)
                seeded = profiles.seed_provider_credentials_from_root(
                    provider, read_user_config_raw(stage / "config.yaml"), profile_dir=stage,
                )
            saved_model, saved_provider = profiles._read_config_model(stage)
            response = {
                "ok": True, "name": name, "path": str(target),
                "model_set": bool(saved_model and saved_provider),
                "seeded_credentials": seeded,
                "template_id": body.template_id, "template_version": manifest.version,
                "generation_checked": False,
            }
            receipt = {
                "request": key_hash, "fingerprint": fingerprint, "response": response,
                "files": {
                    p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in source.rglob("*") if p.is_file()
                },
            }
            atomic_write_text(stage / ".agent-template.json", json.dumps(receipt), create_mode=0o600)
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
