"""K21-317: the speech-recognition key is one per installation.

Under multiplexing a profile reads only its own ``.env``. The owner sets the
Deepgram key once, in the installation root; an agent created later has no key
of its own and used to fall silently to the local whisper.
"""
import pytest

from agent import secret_scope as ss
from korra_cli.config_defaults import DEFAULT_CONFIG
from tools import transcription_tools as tt


@pytest.fixture(autouse=True)
def _reset_multiplex(monkeypatch):
    ss.set_multiplex_active(False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    yield
    ss.set_multiplex_active(False)


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "install"
    (root / "profiles").mkdir(parents=True)
    return root


def _profile(root, name, env=""):
    home = root / "profiles" / name
    home.mkdir()
    (home / ".env").write_text(env, encoding="utf-8")
    return home


def _provider_for(home):
    ss.set_multiplex_active(True)
    token = ss.set_secret_scope(ss.build_profile_secret_scope(home))
    try:
        return tt._get_provider(dict(DEFAULT_CONFIG["stt"]))
    finally:
        ss.reset_secret_scope(token)


def test_new_profile_without_own_key_gets_root_key_and_picks_deepgram(install):
    (install / ".env").write_text("DEEPGRAM_API_KEY=dg-root\n", encoding="utf-8")
    home = _profile(install, "newbie", "TELEGRAM_BOT_TOKEN=t\n")

    assert ss.build_profile_secret_scope(home)["DEEPGRAM_API_KEY"] == "dg-root"
    assert _provider_for(home) == "deepgram"


def test_profile_key_wins_over_root_key(install):
    (install / ".env").write_text("DEEPGRAM_API_KEY=dg-root\n", encoding="utf-8")
    home = _profile(install, "own", "DEEPGRAM_API_KEY=dg-own\n")

    assert ss.build_profile_secret_scope(home)["DEEPGRAM_API_KEY"] == "dg-own"


def test_blank_profile_key_falls_back_to_root_key(install):
    (install / ".env").write_text("DEEPGRAM_API_KEY=dg-root\n", encoding="utf-8")
    home = _profile(install, "blank", "DEEPGRAM_API_KEY=\n")

    assert ss.build_profile_secret_scope(home)["DEEPGRAM_API_KEY"] == "dg-root"


def test_no_key_anywhere_adds_nothing_to_the_scope(install):
    home = _profile(install, "nokey")

    assert "DEEPGRAM_API_KEY" not in ss.build_profile_secret_scope(home)


def test_other_root_secrets_stay_private_to_their_owner(install):
    (install / ".env").write_text(
        "DEEPGRAM_API_KEY=dg-root\nOPENAI_API_KEY=sk-root\nTELEGRAM_BOT_TOKEN=tg-root\n",
        encoding="utf-8",
    )
    home = _profile(install, "newbie")

    assert ss.build_profile_secret_scope(home) == {"DEEPGRAM_API_KEY": "dg-root"}


def test_root_home_scope_is_unchanged(install):
    (install / ".env").write_text("DEEPGRAM_API_KEY=dg-root\n", encoding="utf-8")

    assert ss.build_profile_secret_scope(install) == {"DEEPGRAM_API_KEY": "dg-root"}
