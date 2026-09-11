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
