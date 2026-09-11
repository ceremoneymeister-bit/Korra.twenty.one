"""K21-033: image generation ships to every profile but is opt-in per platform."""

from korra_cli.tools_config import _get_platform_tools


def test_image_gen_is_off_without_platform_opt_in():
    assert "image_gen" not in _get_platform_tools({}, "cli")
    assert "image_gen" not in _get_platform_tools({}, "telegram")


def test_image_gen_can_be_enabled_for_one_platform_only():
    config = {
        "platform_toolsets": {
            "cli": ["file", "image_gen"],
            "telegram": ["file"],
        }
    }
    assert "image_gen" in _get_platform_tools(config, "cli")
    assert "image_gen" not in _get_platform_tools(config, "telegram")


def test_image_gen_opt_in_is_isolated_between_profile_config_files(tmp_path):
    from korra_cli.config import load_config
    from korra_constants import reset_hermes_home_override, set_hermes_home_override

    enabled_home = tmp_path / "enabled-profile"
    disabled_home = tmp_path / "disabled-profile"
    enabled_home.mkdir()
    disabled_home.mkdir()
    (enabled_home / "config.yaml").write_text(
        "platform_toolsets:\n  cli: [file, image_gen]\n  telegram: [file]\n",
        encoding="utf-8",
    )
    (disabled_home / "config.yaml").write_text(
        "platform_toolsets:\n  cli: [file]\n  telegram: [file]\n",
        encoding="utf-8",
    )

    token = set_hermes_home_override(enabled_home)
    try:
        enabled_config = load_config()
    finally:
        reset_hermes_home_override(token)

    token = set_hermes_home_override(disabled_home)
    try:
        disabled_config = load_config()
    finally:
        reset_hermes_home_override(token)

    assert "image_gen" in _get_platform_tools(enabled_config, "cli")
    assert "image_gen" not in _get_platform_tools(enabled_config, "telegram")
    assert "image_gen" not in _get_platform_tools(disabled_config, "cli")
