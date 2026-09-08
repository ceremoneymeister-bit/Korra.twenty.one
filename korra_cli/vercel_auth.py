"""Helpers for reporting Vercel Sandbox authentication state."""

from __future__ import annotations

import os
from dataclasses import dataclass


_TOKEN_TUPLE_VARS = ("VERCEL_TOKEN", "VERCEL_PROJECT_ID", "VERCEL_TEAM_ID")


@dataclass(frozen=True)
class VercelAuthStatus:
    ok: bool
    label: str
    detail_lines: tuple[str, ...]
    display_label: str = ""


def _present(name: str) -> bool:
    return bool(os.getenv(name))


def describe_vercel_auth() -> VercelAuthStatus:
    """Return Vercel auth status without exposing secret values."""

    has_oidc = _present("VERCEL_OIDC_TOKEN")
    token_states = {name: _present(name) for name in _TOKEN_TUPLE_VARS}
    present_token_vars = tuple(name for name, present in token_states.items() if present)
    missing_token_vars = tuple(name for name, present in token_states.items() if not present)

    if has_oidc:
        details = [
            "режим: OIDC",
            "используется: VERCEL_OIDC_TOKEN",
            "OIDC подходит только для разработки; для постоянной работы используйте токен доступа",
        ]
        if present_token_vars:
            details.append(f"также заданы: {', '.join(present_token_vars)}")
        return VercelAuthStatus(
            True, "OIDC token via VERCEL_OIDC_TOKEN", tuple(details),
            "Токен OIDC из VERCEL_OIDC_TOKEN",
        )

    if not missing_token_vars:
        return VercelAuthStatus(
            True,
            "access token + project/team via VERCEL_TOKEN, VERCEL_PROJECT_ID, VERCEL_TEAM_ID",
            (
                "режим: токен доступа",
                "используются: VERCEL_TOKEN, VERCEL_PROJECT_ID, VERCEL_TEAM_ID",
            ),
            "Токен доступа и проект/команда из VERCEL_TOKEN, VERCEL_PROJECT_ID, VERCEL_TEAM_ID",
        )

    if present_token_vars:
        return VercelAuthStatus(
            False,
            f"partial access-token auth (missing {', '.join(missing_token_vars)})",
            (
                "режим: токен доступа настроен не полностью",
                f"заданы: {', '.join(present_token_vars)}",
                f"не хватает: {', '.join(missing_token_vars)}",
                "задайте вместе VERCEL_TOKEN, VERCEL_PROJECT_ID и VERCEL_TEAM_ID",
            ),
            f"Вход настроен не полностью: не хватает {', '.join(missing_token_vars)}",
        )

    return VercelAuthStatus(
        False,
        "not configured",
        (
            "задайте VERCEL_TOKEN, VERCEL_PROJECT_ID и VERCEL_TEAM_ID",
            "только для разработки: можно задать VERCEL_OIDC_TOKEN",
        ),
        "Не настроен",
    )
