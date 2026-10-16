"""K21-336: неявный Camofox включается, только если контейнеру доступно не меньше 3 ГиБ."""

import contextlib
import io

import pytest

import korra_cli.doctor as doctor
from korra_cli import camofox_embedded as emb
from tests.fakes.camofox_embedded import (  # noqa: F401  (фикстуры)
    choose_browser,
    chromium_root,
    data,
    in_profile,
    install,
)

GIB = 1024 ** 3


def limit_memory(monkeypatch, tmp_path, *, v2=None, v1=None):
    """Подменить файлы cgroup; ``None`` — файла нет."""
    files = []
    for name, value in (("memory.max", v2), ("memory.limit_in_bytes", v1)):
        path = tmp_path / "cgroup" / name
        if value is not None:
            path.parent.mkdir(exist_ok=True)
            path.write_text(f"{value}\n")
        files.append(path)
    monkeypatch.setattr(emb, "_CGROUP_MEMORY_FILES", tuple(files), raising=False)


@pytest.mark.parametrize("limit", [int(1.5 * GIB), 2 * GIB, 3 * GIB - 1])
def test_small_container_does_not_get_camofox_implicitly(install, data, monkeypatch, tmp_path, limit):
    from tools import browser_camofox as bc

    limit_memory(monkeypatch, tmp_path, v2=limit)
    assert not emb.uses_embedded({})
    assert bc.get_camofox_url() == ""
    assert not bc.is_camofox_mode()
    emb.sync(signal=False)
    assert not emb.is_enabled()


@pytest.mark.parametrize("limit", [3 * GIB, 8 * GIB, "max"])
def test_enough_memory_or_no_limit_keeps_the_default(install, data, monkeypatch, tmp_path, limit):
    limit_memory(monkeypatch, tmp_path, v2=limit)
    assert emb.uses_embedded({})
    emb.sync(signal=False)
    assert emb.is_enabled()


def test_cgroup_v1_is_read(install, data, monkeypatch, tmp_path):
    limit_memory(monkeypatch, tmp_path, v1=2 * GIB)
    assert not emb.uses_embedded({})
    limit_memory(monkeypatch, tmp_path, v1=3 * GIB)
    assert emb.uses_embedded({})


def test_cgroup_v1_without_limit_is_a_huge_number(install, data, monkeypatch, tmp_path):
    limit_memory(monkeypatch, tmp_path, v1=9223372036854771712)
    assert emb.memory_limit_bytes() is None
    assert emb.uses_embedded({})


@pytest.mark.parametrize("content", [None, "", "не число"])
def test_unreadable_limit_is_the_same_as_no_limit(install, data, monkeypatch, tmp_path, content):
    limit_memory(monkeypatch, tmp_path, v2=content)
    assert emb.memory_limit_bytes() is None
    assert emb.uses_embedded({})


def test_explicit_camofox_works_at_any_memory(install, data, monkeypatch, tmp_path):
    from tools import browser_camofox as bc

    limit_memory(monkeypatch, tmp_path, v2=int(1.5 * GIB))
    choose_browser("Camofox")
    assert emb.uses_embedded({"cloud_provider": "camofox"})
    assert emb.is_enabled()
    assert bc.get_camofox_url() != ""


def test_other_explicit_choices_are_unchanged(install, data, monkeypatch, tmp_path):
    limit_memory(monkeypatch, tmp_path, v2=int(1.5 * GIB))
    assert not emb.uses_embedded({"cloud_provider": "local"})
    assert not emb.uses_embedded({}, external_url="http://camofox.example")
    assert not emb.uses_embedded({}, cdp_url="ws://cdp.example")
    limit_memory(monkeypatch, tmp_path, v2=8 * GIB)
    assert not emb.uses_embedded({"cloud_provider": "local"})


def test_note_names_the_limit_and_the_manual_way(install, data, monkeypatch, tmp_path):
    limit_memory(monkeypatch, tmp_path, v2=int(1.5 * GIB))
    assert emb.default_memory_note() == (
        "Camofox не включён по умолчанию: контейнеру доступно 1,5 ГиБ, нужно от 3 ГиБ; можно выбрать вручную"
    )
    limit_memory(monkeypatch, tmp_path, v2=2 * GIB)
    assert "доступно 2 ГиБ" in emb.default_memory_note()
    limit_memory(monkeypatch, tmp_path, v2=3 * GIB)
    assert emb.default_memory_note() == ""
    limit_memory(monkeypatch, tmp_path, v2="max")
    assert emb.default_memory_note() == ""


def test_status_and_doctor_explain_it(install, data, monkeypatch, tmp_path):
    limit_memory(monkeypatch, tmp_path, v2=int(1.5 * GIB))
    info = emb.status()
    assert info["state"] == "unused"
    assert "доступно 1,5 ГиБ" in info["detail"] and "от 3 ГиБ" in info["detail"]
    assert "Camofox" in info["next"]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        doctor.check_embedded_camofox([])
    assert "доступно 1,5 ГиБ" in out.getvalue()


def test_picker_row_is_not_marked_default_and_carries_the_note(install, data, monkeypatch, tmp_path):
    from korra_cli import tools_config as tc

    limit_memory(monkeypatch, tmp_path, v2=int(1.5 * GIB))
    cat = tc.TOOL_CATEGORIES["browser"]
    rows = tc._visible_providers(cat, {})
    camofox = next(r for r in rows if r["name"] == "Camofox")
    assert "по умолчанию" not in camofox["badge"]
    assert "доступно 1,5 ГиБ" in camofox["tag"]
    assert rows[0]["name"] != "Camofox"
    local = next(r for r in rows if r["name"] == "Local Browser")
    assert local["badge"] == "★ рекомендуется · бесплатно"

    explicit = tc._visible_providers(cat, {"browser": {"cloud_provider": "camofox"}})
    assert "доступно" not in next(r for r in explicit if r["name"] == "Camofox")["tag"]

    limit_memory(monkeypatch, tmp_path, v2=4 * GIB)
    rows = tc._visible_providers(cat, {})
    assert rows[0]["name"] == "Camofox" and "по умолчанию" in rows[0]["badge"]
    assert "доступно" not in rows[0]["tag"]


def test_subscription_view_follows_the_gate(install, data, monkeypatch, tmp_path):
    from korra_cli.nous_subscription import get_nous_subscription_features

    limit_memory(monkeypatch, tmp_path, v2=2 * GIB)
    assert get_nous_subscription_features({}).browser.current_provider != "Camofox"
    limit_memory(monkeypatch, tmp_path, v2=4 * GIB)
    assert get_nous_subscription_features({}).browser.current_provider == "Camofox"
