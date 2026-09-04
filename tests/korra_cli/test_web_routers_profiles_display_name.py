"""PUT /api/profiles/{name}/display-name — человеческое имя для вкладок (Korra 21)."""
import asyncio
from pathlib import Path

import pytest

from korra_cli.profiles import create_profile, get_profile_dir, read_profile_meta


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    default_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    return tmp_path


def _call(name, display_name):
    from korra_cli.web_models import ProfileDisplayNameUpdate
    from korra_cli.web_routers.profiles import update_profile_display_name_endpoint

    return asyncio.run(
        update_profile_display_name_endpoint(name, ProfileDisplayNameUpdate(display_name=display_name))
    )


def test_sets_clears_and_validates_display_name(profile_env):
    create_profile("secretary", no_alias=True)

    assert _call("secretary", "  Секретарь Лена ") == {"ok": True, "display_name": "Секретарь Лена"}
    assert read_profile_meta(get_profile_dir("secretary")).get("display_name") == "Секретарь Лена"

    assert _call("secretary", "") == {"ok": True, "display_name": ""}
    assert not read_profile_meta(get_profile_dir("secretary")).get("display_name")

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as too_long:
        _call("secretary", "x" * 65)
    assert too_long.value.status_code == 400
