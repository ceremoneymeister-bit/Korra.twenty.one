import itertools
import os
import time

import pytest
import yaml

from gateway.credential_management import installation_owners, owner_matches, owner_principal

_TICKS = itertools.count(1)


def _config(platform: str, *owners: object) -> dict:
    return {
        "gateway": {
            "credential_management": {
                "owners": {platform: list(owners)},
            }
        }
    }


def test_ordinary_authorized_or_paired_user_is_not_implicitly_an_owner():
    assert owner_matches({}, "telegram", "paired-user") is False
    assert owner_matches(
        {"gateway": {"credential_management": {"owners": {}}}},
        "telegram",
        "paired-user",
    ) is False


def test_exact_explicit_platform_owner_is_allowed():
    assert owner_matches(_config("telegram", "123"), "telegram", "123") is True


def test_owner_entry_for_another_platform_or_profile_is_denied():
    telegram_profile = _config("telegram", "123")
    other_profile = _config("telegram", "456")

    assert owner_matches(telegram_profile, "discord", "123") is False
    assert owner_matches(other_profile, "telegram", "123") is False


def test_wildcards_and_malformed_owner_maps_fail_closed():
    assert owner_matches(_config("telegram", "*"), "telegram", "123") is False
    assert owner_matches(
        {"gateway": {"credential_management": {"owners": {"telegram": "123"}}}},
        "telegram",
        "123",
    ) is False
    assert owner_matches(_config("telegram", True, ""), "telegram", "1") is False


# ---------------------------------------------------------------------------
# Installation level (decision of 24.09.2026): the owner is a person, named
# once in the installation root's config.yaml and recognized in every profile.
# ---------------------------------------------------------------------------

@pytest.fixture
def installation(tmp_path, monkeypatch):
    """An installation root with two profiles; the root is resolved like the gateway does."""
    import korra_constants

    root = tmp_path / "data"
    for name in ("assistant", "designer"):
        (root / "profiles" / name).mkdir(parents=True)
    monkeypatch.setattr(korra_constants, "get_default_hermes_root", lambda: root)

    def write(path, owners):
        body = {"model": {"default": "x"}}
        if owners is not None:
            body["gateway"] = {"credential_management": {"owners": owners}}
        path.write_text(yaml.safe_dump(body), encoding="utf-8")
        # A distinct, increasing mtime even on coarse filesystems: the config
        # caches key on (mtime_ns, size), and two lists can have one size.
        stamp = time.time_ns() + next(_TICKS) * 1_000_000_000
        os.utime(path, ns=(stamp, stamp))

    return root, write


DM = {"platform": "telegram", "chat_type": "dm", "internal": False}


def test_root_owner_is_recognized_in_the_direct_chat_of_every_profile(installation):
    root, write = installation
    write(root / "config.yaml", {"telegram": ["42"]})
    for name in ("assistant", "designer"):
        write(root / "profiles" / name / "config.yaml", None)  # the profile names nobody
    assert installation_owners("telegram") == {"42"}
    for profile_config in ({}, {"gateway": {}}, _config("telegram", "55")):
        assert owner_matches(profile_config, "telegram", "42") is True
        assert owner_principal(profile_config, user_id="42", **DM) == "live"
        assert owner_principal(profile_config, user_id="42", **{**DM, "internal": True}) == "delegated"


def test_root_owner_does_not_open_groups_or_other_senders(installation):
    root, write = installation
    write(root / "config.yaml", {"telegram": ["42"]})
    assert owner_principal({}, user_id="777", **DM) == ""
    assert owner_principal({}, user_id="42", **{**DM, "chat_type": "group"}) == ""
    assert owner_principal({}, user_id="42", **{**DM, "chat_type": "channel"}) == ""
    assert owner_matches({}, "discord", "42") is False  # another platform's principal


def test_root_list_fails_closed_like_the_profile_list(installation):
    root, write = installation
    write(root / "config.yaml", {"telegram": ["*", True, ""]})
    assert owner_matches({}, "telegram", "42") is False
    write(root / "config.yaml", {"telegram": "42"})  # a scalar is not a list
    assert owner_matches({}, "telegram", "42") is False
    (root / "config.yaml").write_text("gateway: [unparseable", encoding="utf-8")
    assert owner_matches({}, "telegram", "42") is False
    assert owner_matches(_config("telegram", "55"), "telegram", "55") is True


def test_profile_list_still_works_and_stays_its_own(installation):
    root, write = installation
    write(root / "config.yaml", None)
    profile = _config("telegram", "55")
    assert owner_principal(profile, user_id="55", **DM) == "live"
    assert owner_principal({}, user_id="55", **DM) == ""  # another profile does not inherit it
    write(root / "config.yaml", {"telegram": ["42"]})
    assert owner_principal(profile, user_id="55", **DM) == "live"
    assert owner_principal(profile, user_id="42", **DM) == "live"


@pytest.mark.parametrize("root_owners", [None, {}, {"telegram": []}, {"discord": ["42"]}])
def test_empty_root_keeps_the_previous_behavior(installation, root_owners):
    root, write = installation
    write(root / "config.yaml", root_owners)
    assert owner_principal({}, user_id="42", **DM) == ""
    assert owner_principal(_config("telegram", "42"), user_id="42", **DM) == "live"
    (root / "config.yaml").unlink()  # no root config at all
    assert owner_principal({}, user_id="42", **DM) == ""
    assert owner_principal(_config("telegram", "42"), user_id="42", **DM) == "live"


def test_explicit_installation_mapping_needs_no_disk(installation):
    root, write = installation
    write(root / "config.yaml", {"telegram": ["42"]})
    assert owner_matches({}, "telegram", "42", installation={}) is False
    assert owner_matches({}, "telegram", "7", installation=_config("telegram", "7")) is True


def test_an_edit_of_the_root_applies_to_the_next_decision_without_restart(installation):
    """The gateway asks per message; the mtime-keyed cache must not pin the old list."""
    root, write = installation
    write(root / "config.yaml", None)
    assert owner_matches({}, "telegram", "42") is False
    write(root / "config.yaml", {"telegram": ["42"]})
    assert owner_matches({}, "telegram", "42") is True
    write(root / "config.yaml", {"telegram": ["43"]})
    assert owner_matches({}, "telegram", "42") is False


def test_the_home_override_of_the_serving_profile_survives_the_root_read(installation):
    from korra_constants import (
        get_hermes_home,
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    root, write = installation
    write(root / "config.yaml", {"telegram": ["42"]})
    profile_home = root / "profiles" / "designer"
    token = set_hermes_home_override(str(profile_home))
    try:
        assert owner_matches({}, "telegram", "42") is True
        assert get_hermes_home() == profile_home
    finally:
        reset_hermes_home_override(token)


def test_a_changed_owner_verdict_rebuilds_the_cached_agent():
    """Owner-only tool schemas are frozen into the cached agent by this verdict."""
    from gateway.run import GatewayRunner

    runtime = {"provider": "openrouter", "base_url": "", "api_mode": ""}

    def signature(**kwargs):
        return GatewayRunner._agent_config_signature("m", runtime, ["hermes-telegram"], "", **kwargs)

    assert signature() == signature(acts_for_owner=False)
    assert signature(acts_for_owner=True) != signature(acts_for_owner=False)
    assert signature(acts_for_owner=True) == signature(acts_for_owner=True)
