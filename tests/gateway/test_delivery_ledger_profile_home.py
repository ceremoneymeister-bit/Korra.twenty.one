"""Delivery ledger rows written under a profile override stay visible to the boot sweep (K21-274)."""

from gateway import delivery_ledger as dl
from korra_constants import reset_hermes_home_override, set_hermes_home_override


def test_ledger_lives_in_the_process_home_whatever_the_profile_override(tmp_path, monkeypatch):
    root = tmp_path / "root"
    profile = tmp_path / "root" / "profiles" / "second"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))

    assert dl._db_path() == root / "state.db"

    token = set_hermes_home_override(profile)
    try:
        assert dl._db_path() == root / "state.db"
    finally:
        reset_hermes_home_override(token)


def test_row_recorded_under_a_profile_override_is_swept_from_the_launch_context(tmp_path, monkeypatch):
    root = tmp_path / "root"
    profile = root / "profiles" / "second"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))

    token = set_hermes_home_override(profile)
    try:
        dl.record_obligation(
            obligation_id="ob-profile",
            session_key="agent:second:telegram:dm:1",
            platform="telegram",
            chat_id="1",
            thread_id=None,
            content="cut-off reply",
            adapter_profile="second",
        )
    finally:
        reset_hermes_home_override(token)

    assert (root / "state.db").exists()
    assert not (profile / "state.db").exists()
    with dl._connect() as conn:
        row = conn.execute(
            "SELECT adapter_profile FROM delivery_obligations WHERE obligation_id='ob-profile'"
        ).fetchone()
    assert row == ("second",)
