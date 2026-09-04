"""Сторож канонических значений форка Korra.

Зачем этот файл существует
--------------------------
В прошлом форке мы потеряли собственные дефолты молча. Апстрим вынес
``DEFAULT_CONFIG`` в новый модуль, мерж прошёл БЕЗ ЕДИНОГО КОНФЛИКТА и вернул
``language: en``, погасил streaming и RU-дефолты. Счётчик конфликтов этого не
показывает: изменение приезжает без конфликта, поэтому его не видит ни один
резолвер.

Мерило риска — не число закрытых конфликтов, а диф ЭФФЕКТИВНОГО результата.
Этот тест и есть такое мерило: он падает, когда каноническое значение форка
меняется, независимо от того, чья правка это сделала.

Каждое утверждение ниже — решение, а не вкус. Меняя значение, меняйте и тест
осознанно, вместе с записью в CHECKPOINTS.md.
"""

import re

import pytest

from korra_cli.config_defaults import DEFAULT_CONFIG


def test_language_is_russian():
    """Продукт русскоязычный: владелец и все клиенты.

    Апстримный дефолт "en" означал, что свежепоставленный контур разговаривает
    с клиентом по-английски, пока кто-то не вспомнит про настройку.
    """
    assert DEFAULT_CONFIG["display"]["language"] == "ru"


def test_remote_model_catalog_is_off():
    """Удалённый каталог моделей обновлялся с сайта апстрима раз в час и мог
    МОЛЧА сменить дефолтную модель на контуре клиента — чужое решение с
    денежными последствиями. Включать только вместе с подменой url на свой.
    """
    assert DEFAULT_CONFIG["model_catalog"]["enabled"] is False


def test_no_upstream_hosts_in_model_catalog_chain():
    """Запасная цепочка вела на raw.githubusercontent апстрима — вторая дорога
    к смене дефолтной модели в обход основного url.
    """
    from korra_cli import model_catalog

    for url in model_catalog.DEFAULT_CATALOG_FALLBACK_URLS:
        assert "nousresearch" not in url.lower(), f"источник апстрима в цепочке: {url}"


def test_no_upstream_hosts_in_primary_catalog_urls():
    """Аудит: первый сторож проверял только fallback-массив, а основной url
    оставался апстримным и в коде, и в DEFAULT_CONFIG. Три двери, одна
    охранялась.
    """
    from korra_cli import model_catalog

    assert "nousresearch" not in model_catalog.DEFAULT_CATALOG_URL.lower()
    assert "nousresearch" not in DEFAULT_CONFIG["model_catalog"]["url"].lower()


def test_catalog_config_fails_closed(monkeypatch):
    """Аудит воспроизвёл: исключение при чтении config.yaml давало
    enabled=True с url апстрима — сбой РАСШИРЯЛ сетевую поверхность.
    Сломанный конфиг обязан оставлять каталог выключенным.
    """
    from korra_cli import config as config_mod
    from korra_cli import model_catalog

    def boom():
        raise RuntimeError("битый config.yaml")

    monkeypatch.setattr(config_mod, "load_config", boom)
    cfg = model_catalog._load_catalog_config()
    assert cfg["enabled"] is False
    assert "nousresearch" not in cfg["url"].lower()


def test_disabled_catalog_ignores_stale_cache(monkeypatch, tmp_path):
    """Аудит воспроизвёл: при enabled=False старый дисковый кэш продолжал
    подменять тихую дефолтную модель. Выключено — значит выключено.
    """
    import json

    from korra_cli import model_catalog

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "model_catalog.json").write_text(json.dumps({
        "providers": {"openrouter": {"models": [
            {"id": "stale/expensive", "default": True},
        ]}},
    }), encoding="utf-8")
    monkeypatch.setattr(model_catalog, "_cache_path", lambda: cache / "model_catalog.json")
    monkeypatch.setattr(model_catalog, "_load_catalog_config", lambda: {
        "enabled": False, "url": "", "ttl_hours": 1, "providers": {},
    })
    model_catalog.reset_cache()
    assert model_catalog.get_default_model_from_cache("openrouter") is None


def test_centralized_skills_index_is_off():
    """Индекс скиллов апстрима показывал их новые скиллы как «встроенные»
    и позволял поставить исполняемый код без нашего коммита.
    """
    from tools import skills_hub

    assert skills_hub.HERMES_INDEX_URL == ""


def test_mcp_cimd_is_off():
    """CIMD-документ апстрима определял, каким OAuth-клиентом мы
    представляемся стороннему MCP-серверу и какие redirect-порты объявляем.
    Пустое значение уводит потоки на DCR — штатный запасной путь.
    """
    from tools import mcp_oauth

    assert mcp_oauth._CIMD_CLIENT_METADATA_URL == ""


@pytest.mark.parametrize("locale_name", ["ru"])
def test_locale_is_actually_translated(locale_name):
    """Локали приезжают структурно полными, но непереведёнными.

    Паритет ключей держится, а значения остаются английскими — так у нас уже
    было с блоком /context в 0.20. Порог намеренно с запасом: тест ловит
    массовый регресс (например, откат файла к апстримной версии), а не
    единичные служебные строки.
    """
    import yaml

    from korra_cli import config_defaults  # noqa: F401  (гарантия импортируемости пакета)

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    data = yaml.safe_load((root / "locales" / f"{locale_name}.yaml").read_text(encoding="utf-8"))

    def flat(d, prefix=""):
        for key, value in (d or {}).items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                yield from flat(value, name)
            else:
                yield name, value

    cyrillic = re.compile("[А-Яа-яЁё]")
    values = [v for _, v in flat(data) if isinstance(v, str) and v.strip()]
    without = [v for v in values if not cyrillic.search(v)]

    assert len(without) <= 25, (
        f"в {locale_name}.yaml {len(without)} значений без кириллицы — "
        "похоже на откат перевода к апстримной версии"
    )
