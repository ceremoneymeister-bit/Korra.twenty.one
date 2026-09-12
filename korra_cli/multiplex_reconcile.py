"""Мультиплекс профилей: инспекция и ремонт трёх условий вкладок агентов.

Korra 21 показывает каждого агента вкладкой в панели. Вкладка ходит в
api-сервер по префиксу ``/p/<профиль>/``, а движок обслуживает чужой префикс
только когда корневой шлюз мультиплексирует профили. Для рабочей вкладки
нужны **три** вещи одновременно (K21-058, контур Виктории 12.09.2026):

1. ``gateway.multiplex_profiles: true`` в корневом ``config.yaml``;
2. корневой ``API_SERVER_KEY`` в ``.env`` каждого именованного профиля —
   мультиплексор аутентифицирует ``/p/<профиль>/`` ключом самого профиля;
3. ``platforms.api_server.enabled: false`` в ``config.yaml`` каждого
   профиля — иначе наличие ключа включает профилю собственный api-сервер, и
   мультиплексор выбрасывает профиль целиком
   («Skipping secondary profile … port-binding config error»).

Пропуск любого пункта выглядит для владельца одинаково: «Корра не смогла
ответить» во всех вкладках, кроме основной, при живом Telegram.

Свежая установка получает флаг из ``korra-config.yaml.example``, а профиль,
созданный через ``korra profile create``, — пин и ключ из ``profiles.py``.
Контур, приехавший с 0.20.x, не получал ничего: этот модуль закрывает
именно его. :func:`inspect_multiplex` только читает и годится для
``korra doctor``; :func:`reconcile_multiplex` чинит идемпотентно и вызывается
на старте контейнера до подъёма шлюзов.

Ограничение по замыслу: явное ``gateway.multiplex_profiles: false`` —
решение владельца, оно не переворачивается. Переворачивается только
отсутствие ключа.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

MISSING_KEY = "api_server_key"
MISSING_PIN = "api_server_pin"
GATEWAY_RUNNING = "own_gateway_running"

_HUMAN = {
    MISSING_KEY: "в .env нет корневого API_SERVER_KEY",
    MISSING_PIN: "в config.yaml нет platforms.api_server.enabled: false",
    GATEWAY_RUNNING: "собственный шлюз профиля отмечен как running (конфликт с общим шлюзом)",
}

_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass
class ProfileFinding:
    name: str
    missing: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing

    def describe(self) -> str:
        return "; ".join(_HUMAN[item] for item in self.missing)


@dataclass
class MultiplexReport:
    hermes_home: Path
    flag: Optional[bool]              # True / False / None (ключа нет)
    root_key_present: bool
    profiles: List[ProfileFinding] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def named_profiles(self) -> int:
        return len(self.profiles)

    @property
    def broken_profiles(self) -> List[ProfileFinding]:
        return [p for p in self.profiles if not p.ok]

    @property
    def tabs_work(self) -> bool:
        """Вкладки агентов будут отвечать при таком состоянии файлов."""
        if self.flag is not True:
            return self.named_profiles == 0
        return self.root_key_present and not self.broken_profiles


# ── чтение ───────────────────────────────────────────────────────────────────


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        from korra_cli.config import read_user_config_raw

        data = read_user_config_raw(path)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("multiplex: %s не прочитан: %s", path, exc)
        return {}


def read_multiplex_flag(root_config: Dict[str, Any]) -> Optional[bool]:
    """``True``/``False`` при явном значении, ``None`` когда ключа нет."""
    for value in (
        root_config.get("multiplex_profiles"),
        (root_config.get("gateway") or {}).get("multiplex_profiles")
        if isinstance(root_config.get("gateway"), dict) else None,
    ):
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            token = value.strip().lower()
            if token in {"1", "true", "yes", "on"}:
                return True
            if token in {"0", "false", "no", "off"}:
                return False
        if isinstance(value, int):
            return bool(value)
    return None


def _env_value(env_path: Path, key: str) -> str:
    if not env_path.is_file():
        return ""
    try:
        from agent.secret_scope import load_env_file

        return str(load_env_file(env_path).get(key) or "").strip()
    except Exception as exc:
        log.warning("multiplex: %s не прочитан: %s", env_path, exc)
        return ""


def _api_server_pinned_off(profile_config: Dict[str, Any]) -> bool:
    platforms = profile_config.get("platforms")
    if not isinstance(platforms, dict):
        return False
    api_server = platforms.get("api_server")
    return isinstance(api_server, dict) and api_server.get("enabled") is False


def _desired_gateway_state(profile_dir: Path) -> Optional[str]:
    state_file = profile_dir / "gateway_state.json"
    if not state_file.is_file():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    desired = data.get("desired_state")
    if desired is not None:
        return str(desired)
    state = data.get("gateway_state")
    if state in {"running", "draining"}:
        return "running"
    return str(state) if state is not None else None


def iter_named_profiles(hermes_home: Path):
    """Именованные профили, которые обслуживал бы мультиплексор."""
    from korra_constants import named_profile_is_deleted

    root = hermes_home / "profiles"
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name == "default":
            continue
        if not _PROFILE_ID_RE.match(entry.name):
            continue
        if not (entry / "SOUL.md").exists():
            continue
        if named_profile_is_deleted(entry):
            continue
        yield entry


def inspect_multiplex(hermes_home: Path) -> MultiplexReport:
    """Только чтение: что не хватает каждому профилю для вкладки в панели."""
    hermes_home = Path(hermes_home)
    root_config = _read_yaml(hermes_home / "config.yaml")
    flag = read_multiplex_flag(root_config)
    root_key = _env_value(hermes_home / ".env", "API_SERVER_KEY")
    report = MultiplexReport(
        hermes_home=hermes_home, flag=flag, root_key_present=bool(root_key)
    )
    for profile_dir in iter_named_profiles(hermes_home):
        finding = ProfileFinding(name=profile_dir.name)
        profile_key = _env_value(profile_dir / ".env", "API_SERVER_KEY")
        if not profile_key or (root_key and profile_key != root_key):
            finding.missing.append(MISSING_KEY)
        if not _api_server_pinned_off(_read_yaml(profile_dir / "config.yaml")):
            finding.missing.append(MISSING_PIN)
        if _desired_gateway_state(profile_dir) == "running":
            finding.missing.append(GATEWAY_RUNNING)
        report.profiles.append(finding)
    return report


# ── запись ───────────────────────────────────────────────────────────────────


def _write_in_place(path: Path, text: str) -> None:
    """Перезаписать файл в тот же inode: владелец и права контура не меняются."""
    with open(path, "r+" if path.exists() else "w", encoding="utf-8") as handle:
        handle.seek(0)
        handle.write(text)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())


def _set_env_key(env_path: Path, key: str, value: str) -> None:
    from korra_cli.config import _quote_env_value

    line = f"{key}={_quote_env_value(value)}"
    existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    lines = existing.splitlines()
    key_line = re.compile(rf"^(export\s+)?{re.escape(key)}\s*=")
    for index, candidate in enumerate(lines):
        if key_line.match(candidate):
            lines[index] = line
            break
    else:
        lines.append(line)
    new_text = "\n".join(lines) + "\n"
    created = not env_path.exists()
    _write_in_place(env_path, new_text)
    if created:
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass


def _pin_api_server_off(profile_dir: Path) -> None:
    config_path = profile_dir / "config.yaml"
    if config_path.is_file():
        from utils import atomic_roundtrip_yaml_update

        atomic_roundtrip_yaml_update(config_path, "platforms.api_server.enabled", False)
        return
    import yaml

    _write_in_place(
        config_path,
        yaml.safe_dump({"platforms": {"api_server": {"enabled": False}}}, sort_keys=False),
    )


def _mark_gateway_stopped(profile_dir: Path) -> None:
    state_file = profile_dir / "gateway_state.json"
    data: Dict[str, Any] = {}
    if state_file.is_file():
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    data["desired_state"] = "stopped"
    if data.get("gateway_state") in {"running", "draining"}:
        data["gateway_state"] = "stopped"
    data["multiplex_note"] = (
        "own gateway parked: the root gateway multiplexes this profile (K21-058)"
    )
    _write_in_place(state_file, json.dumps(data, ensure_ascii=False) + "\n")


def _set_root_flag(hermes_home: Path) -> None:
    config_path = hermes_home / "config.yaml"
    if config_path.is_file():
        from utils import atomic_roundtrip_yaml_update

        atomic_roundtrip_yaml_update(config_path, "gateway.multiplex_profiles", True)
        return
    import yaml

    _write_in_place(
        config_path,
        yaml.safe_dump({"gateway": {"multiplex_profiles": True}}, sort_keys=False),
    )


def reconcile_multiplex(
    hermes_home: Path,
    *,
    dry_run: bool = False,
    enable_when_absent: bool = True,
) -> MultiplexReport:
    """Привести контур к рабочим вкладкам агентов; идемпотентно.

    - Ключ ``gateway.multiplex_profiles`` отсутствует → включить (0.20.x-контур,
      обновлённый до Korra 21). Явное ``false`` не трогается.
    - Флаг включён → каждому профилю: корневой ключ в ``.env``, пин
      ``platforms.api_server.enabled: false``, собственный шлюз переводится в
      ``desired_state: stopped``, чтобы boot-reconcile не поднял второго
      владельца тех же ботов.

    Возвращает отчёт **после** ремонта; ``actions`` перечисляет сделанное,
    ``errors`` — что не удалось (файл только для чтения и т. п.).
    """
    hermes_home = Path(hermes_home)
    report = inspect_multiplex(hermes_home)

    # An operator who forces GATEWAY_MULTIPLEX_PROFILES=false wants separate
    # per-profile gateways; parking them would contradict that intent.
    try:
        from gateway.config import _env_multiplex_profiles_override

        if _env_multiplex_profiles_override() is False:
            return report
    except Exception:  # pragma: no cover - env probe must not block repair
        pass

    # Only a contour that already has a root config.yaml is a "0.20.x
    # contour without the flag". A home with no config at all has not been
    # seeded yet (stage2 seeds the Korra template, which carries the flag) —
    # inventing config there would surprise non-container layouts.
    if not (hermes_home / "config.yaml").is_file():
        enable_when_absent = False

    if report.flag is None and enable_when_absent:
        action = "root config.yaml: gateway.multiplex_profiles: true (ключа не было)"
        if dry_run:
            report.actions.append(f"[dry-run] {action}")
        else:
            try:
                _set_root_flag(hermes_home)
                report.actions.append(action)
            except Exception as exc:
                report.errors.append(f"{action}: {exc}")
        report = _merge_actions(report, inspect_multiplex(hermes_home) if not dry_run else None)

    if report.flag is not True and not (dry_run and report.flag is None and enable_when_absent):
        return report

    root_key = _env_value(hermes_home / ".env", "API_SERVER_KEY")
    if not root_key:
        report.errors.append(
            "в корневом .env нет API_SERVER_KEY — ключ профилям раздать нечем"
        )

    for finding in report.profiles:
        profile_dir = hermes_home / "profiles" / finding.name
        for item in list(finding.missing):
            if item == MISSING_KEY:
                if not root_key:
                    continue
                action = f"{finding.name}/.env: API_SERVER_KEY = корневой ключ"
                op = lambda: _set_env_key(profile_dir / ".env", "API_SERVER_KEY", root_key)  # noqa: E731
            elif item == MISSING_PIN:
                action = f"{finding.name}/config.yaml: platforms.api_server.enabled: false"
                op = lambda: _pin_api_server_off(profile_dir)  # noqa: E731
            elif item == GATEWAY_RUNNING:
                action = f"{finding.name}/gateway_state.json: desired_state: stopped"
                op = lambda: _mark_gateway_stopped(profile_dir)  # noqa: E731
            else:  # pragma: no cover - защитная ветка
                continue
            if dry_run:
                report.actions.append(f"[dry-run] {action}")
                continue
            try:
                op()
                report.actions.append(action)
            except Exception as exc:
                report.errors.append(f"{action}: {exc}")

    if dry_run:
        return report
    return _merge_actions(report, inspect_multiplex(hermes_home))


def _merge_actions(previous: MultiplexReport, fresh: Optional[MultiplexReport]) -> MultiplexReport:
    if fresh is None:
        return previous
    fresh.actions = list(previous.actions)
    fresh.errors = list(previous.errors)
    return fresh


def multiplex_effective(hermes_home: Path) -> bool:
    """Обслуживает ли корневой шлюз профили: env-override важнее config.yaml."""
    from gateway.config import _env_multiplex_profiles_override

    env_override = _env_multiplex_profiles_override()
    if env_override is not None:
        return env_override
    return read_multiplex_flag(_read_yaml(Path(hermes_home) / "config.yaml")) is True


__all__ = [
    "GATEWAY_RUNNING",
    "MISSING_KEY",
    "MISSING_PIN",
    "MultiplexReport",
    "ProfileFinding",
    "inspect_multiplex",
    "iter_named_profiles",
    "multiplex_effective",
    "read_multiplex_flag",
    "reconcile_multiplex",
]
