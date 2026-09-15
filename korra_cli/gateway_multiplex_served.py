"""Какие профили ведёт ЖИВОЙ общий шлюз — один ответ для CLI, API и панели.

При ``gateway.multiplex_profiles`` один шлюз основного профиля обслуживает
все остальные: их чат, их Telegram, их ``/p/<профиль>/``. Такой профиль не
пишет ни ``gateway.pid``, ни ``gateway_state.json``, поэтому проверка «есть ли
процесс у этого профиля» честно отвечает «нет» — и владелец видит
«остановлен» у агента, который в этот момент ему отвечает (K21-088).

Правда о составе — у самого процесса: ``gateway/run.py`` при старте пишет в
``gateway_state.json`` основного профиля ключ ``served_profiles``. Конфиг и
``GATEWAY_MULTIPLEX_PROFILES``, какими их видит **CLI**, — только догадка:
``korra -p alpha …`` читает ``.env`` профиля alpha, а ``multiplex_profile_allowlist``
могли поправить уже после запуска. Поэтому запись живого шлюза важнее конфига,
а вывод из конфига остаётся откатом для шлюза, который запись ещё не писал.

Модуль намеренно маленький и без тяжёлых импортов: его зовёт
``gateway.status`` из ступени liveness, которую дёргают на каждый опрос
панели.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def live_default_gateway_pid() -> Optional[int]:
    """PID шлюза основного профиля, если его личность подтверждена, иначе None.

    ``gateway.status.live_gateway_pid_for_home``: PID-файл с блокировкой, затем
    запись самого шлюза, и обе — против живой таблицы процессов (время старта,
    командная строка шлюза, свой home). Шлюз под супервизором может жить вообще
    без ``gateway.pid``, а протухшая запись с переиспользованным PID не должна
    делать свой ``served_profiles`` действующим.
    """
    try:
        from gateway.status import live_gateway_pid_for_home
        from korra_constants import get_default_hermes_root

        # Read-side probe on a hot path: the dashboard asks about the SAME
        # default home once per profile on every sidebar poll.
        return live_gateway_pid_for_home(get_default_hermes_root(), use_cache=True)
    except Exception:
        logger.debug("live default gateway probe failed", exc_info=True)
        return None


def recorded_served_profiles(default_root: Optional[Path] = None) -> Optional[list[str]]:
    """``served_profiles``, записанные живым общим шлюзом, или None.

    None — ключа нет: запись от шлюза старее этой возможности, либо шлюза нет.
    Пустой список — осмысленный ответ «никого, кроме себя»; откатываться на
    вывод из конфига в этом случае нельзя.
    """
    try:
        from gateway.status import read_runtime_status
        from korra_constants import get_default_hermes_root

        root = Path(default_root) if default_root is not None else get_default_hermes_root()
        runtime = read_runtime_status(root / "gateway_state.json")
        served = (runtime or {}).get("served_profiles")
    except Exception:
        logger.debug("served_profiles read failed", exc_info=True)
        return None
    if not isinstance(served, list):
        return None
    return [str(item) for item in served if item]


def _config_says_served(canon: str, default_root: Path) -> bool:
    """Откат для шлюза без записи ``served_profiles``: конфиг + allowlist.

    ``multiplex_profile_allowlist`` — решение владельца о том, кого общий шлюз
    вообще берёт; профиль вне списка не обслуживается и обязан выглядеть
    остановленным.
    """
    from gateway.config import (
        _env_multiplex_profiles_override,
        _normalize_multiplex_profile_allowlist,
    )

    cfg_path = default_root / "config.yaml"
    cfg = {}
    if cfg_path.exists():
        from korra_cli.config import read_user_config_raw

        cfg = read_user_config_raw(cfg_path) or {}

    env_multiplex = _env_multiplex_profiles_override()
    if env_multiplex is False:
        return False
    if env_multiplex is not True:
        if not cfg_path.exists():
            return False
        if not (
            cfg.get("multiplex_profiles")
            or (cfg.get("gateway", {}) or {}).get("multiplex_profiles")
        ):
            return False

    gateway_cfg = cfg.get("gateway", {}) or {}
    if "multiplex_profile_allowlist" in cfg:
        raw_allowlist = cfg.get("multiplex_profile_allowlist")
    else:
        raw_allowlist = gateway_cfg.get("multiplex_profile_allowlist")
    allowlist = _normalize_multiplex_profile_allowlist(raw_allowlist)
    return allowlist is None or canon in allowlist


def profile_served_by_live_multiplexer(profile: str) -> bool:
    """Ведёт ли живой общий шлюз именно этот именованный профиль прямо сейчас.

    Требуется всё сразу: подтверждённый живой шлюз основного профиля и
    фактическое членство профиля в его составе. Основной профиль сам себя не
    «обслуживает» — у него собственный шлюз, это отдельное состояние.
    """
    try:
        from korra_cli.profiles import normalize_profile_name

        canon = normalize_profile_name(str(profile or "").strip())
    except Exception:
        return False
    if not canon or canon == "default":
        return False

    try:
        if live_default_gateway_pid() is None:
            return False

        recorded = recorded_served_profiles()
        if recorded is not None:
            return canon in {normalize_profile_name(name) for name in recorded}

        from korra_constants import get_default_hermes_root

        return _config_says_served(canon, get_default_hermes_root())
    except Exception:
        logger.debug("multiplexer-serving probe failed for %r", profile, exc_info=True)
        return False


__all__ = [
    "live_default_gateway_pid",
    "profile_served_by_live_multiplexer",
    "recorded_served_profiles",
]
