from gateway.credential_management import owner_matches


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
