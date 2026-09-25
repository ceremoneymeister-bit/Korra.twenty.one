"""«Ключи и доступы» keep Unicode logins, passwords and URLs; only API keys go ASCII.

Nagrada, 25.09.2026: the 1C login «нюра» written through PUT /api/env was
silently saved empty, because every value was stripped to ASCII.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr("korra_cli.config.is_managed", lambda: False)
    return root


def _read(home, key):
    from dotenv import dotenv_values

    return dotenv_values(home / ".env").get(key)


@pytest.mark.parametrize("key,value", [
    ("ONEC_USERNAME", "нюра"),
    ("ONEC_PASSWORD", "пароль 2026"),
    ("ONEC_BASE_URL", "https://база.награда.рф/ut11/odata/standard.odata"),
    ("COMPANY_NAME", "ООО «Награда»"),
])
def test_unicode_values_survive_the_round_trip(home, key, value):
    from korra_cli.config import load_env, save_env_value

    save_env_value(key, value)
    assert _read(home, key) == value
    assert load_env().get(key) == value


def test_api_keys_still_lose_lookalike_glyphs(home, capsys):
    from korra_cli.config import save_env_value

    save_env_value("OPENAI_API_KEY", "sk-abcс123")  # Cyrillic «с» pasted from a PDF
    assert _read(home, "OPENAI_API_KEY") == "sk-abc123"
    assert "не из ASCII" in capsys.readouterr().err


def test_a_key_made_only_of_non_ascii_is_refused_not_saved_empty(home):
    from korra_cli.config import save_env_value

    with pytest.raises(ValueError, match="не похоже на ключ"):
        save_env_value("DEEPGRAM_API_KEY", "секрет")
    assert _read(home, "DEEPGRAM_API_KEY") is None
