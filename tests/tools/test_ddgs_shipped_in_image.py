"""Бесключевой поиск обязан ехать в образе, а не «устанавливаться при первом запросе».

Апстрим держит ``ddgs`` вне зависимостей: пакет тянет native ``primp``, и на
чужих платформах это не всегда собирается. У нас другая реальность — в
опубликованном образе lazy-install выключен (``HERMES_DISABLE_LAZY_INSTALLS=1``),
``/opt/hermes`` только на чтение, ключей поиска нет ни на одном контуре. Поэтому
объявленный бесключевой бэкенд без пакета в образе означает ровно одно:
``ModuleNotFoundError: No module named 'ddgs'`` на каждый запрос владельца
(12.09.2026 — 13 раз за один разговор у Голди, 240 раз на korra21).

Тест держит три половины одного обещания: пакет объявлен точной версией, он
попадает в lock и его extra стоит в ``uv sync`` образа.
"""

from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _ddgs_pins() -> list:
    """Все объявления ddgs в pyproject: core-зависимости и любые extras."""
    project = _pyproject()["project"]
    specs = list(project.get("dependencies") or [])
    for extra_specs in (project.get("optional-dependencies") or {}).values():
        specs.extend(extra_specs)
    return [
        spec for spec in specs
        if re.match(r"^ddgs\s*(\[|==|$)", spec.split(";", 1)[0].strip())
    ]


def test_ddgs_is_declared_with_an_exact_pin():
    pins = _ddgs_pins()
    assert pins, (
        "ddgs нигде не объявлен в pyproject.toml — бесключевой поиск в образе "
        "падает с ModuleNotFoundError"
    )
    for spec in pins:
        assert "==" in spec, f"ddgs должен быть закреплён точной версией, а не {spec!r}"


def test_ddgs_resolves_in_uv_lock():
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert re.search(r'\[\[package\]\]\nname = "ddgs"\nversion = "[^"]+"', lock), (
        "ddgs нет в uv.lock — сборка образа идёт с --frozen и пакет не поставится"
    )


def test_image_build_installs_the_ddgs_extra():
    """Если ddgs живёт в extra, эта extra обязана стоять в uv sync образа."""
    project = _pyproject()["project"]
    if any(
        re.match(r"^ddgs\s*(\[|==|$)", spec.split(";", 1)[0].strip())
        for spec in (project.get("dependencies") or [])
    ):
        return  # core-зависимость ставится всегда, extra не нужна

    owning_extras = [
        name
        for name, specs in (project.get("optional-dependencies") or {}).items()
        if any(
            re.match(r"^ddgs\s*(\[|==|$)", spec.split(";", 1)[0].strip())
            for spec in specs
        )
    ]
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    sync_lines = [
        line for line in dockerfile.splitlines()
        if "uv sync" in line and "--frozen" in line
    ]
    assert sync_lines, "в Dockerfile не нашлась строка uv sync --frozen"
    installed = " ".join(sync_lines)
    assert any(f"--extra {extra}" in installed for extra in owning_extras), (
        f"ни одна из extras {owning_extras} не ставится в образе: {installed!r}"
    )
