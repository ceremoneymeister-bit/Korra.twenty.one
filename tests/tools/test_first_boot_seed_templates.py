"""Засев первого запуска не должен спорить с дефолтами Korra.

Контур создаётся один раз: файл `config.yaml`, скопированный на первой
загрузке, дальше принадлежит контуру и обновлением образа не переписывается.
Поэтому всё, что попало в шаблон засева, застывает в контуре навсегда — со
значениями того дня, когда его раскатали.

Апстримовый `cli-config.yaml.example` перечисляет почти все ключи движка, и
как шаблон засева он молча перекрывал дефолты Korra: свежая раскатка получала
распознавание речи на английском, модель whisper «base» вместо вшитой в образ
и модель ответа через OpenRouter. Здесь закреплено, что засев берёт короткие
шаблоны Korra и что они не переопределяют то, что уже верно в дефолтах.
"""

from pathlib import Path

import pytest
import yaml

from korra_cli.config_defaults import DEFAULT_CONFIG

REPO = Path(__file__).resolve().parents[2]
KORRA_CONFIG_TEMPLATE = REPO / "korra-config.yaml.example"
KORRA_ENV_TEMPLATE = REPO / "korra-env.example"
STAGE2_HOOK = REPO / "docker" / "stage2-hook.sh"


@pytest.fixture(scope="module")
def template() -> dict:
    data = yaml.safe_load(KORRA_CONFIG_TEMPLATE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "шаблон должен разбираться в словарь"
    return data


class TestSeedTemplateAgreesWithDefaults:
    def test_template_does_not_pin_speech_recognition(self, template):
        """Речь настроена в дефолтах: язык, вшитая модель, запрет докачки."""
        assert "stt" not in template, (
            "шаблон засева фиксирует stt — контур застрянет на значениях дня "
            "раскатки и не получит правок из образа"
        )
        stt = DEFAULT_CONFIG["stt"]
        assert stt["language"] == "ru"
        assert stt["local"]["model"] == "medium"
        assert stt["local"]["allow_download"] is False

    def test_template_does_not_pin_a_provider(self, template):
        """Провайдер у каждого контура свой — в шаблоне только пример."""
        assert "model" not in template
        assert "custom_providers" not in template
        text = KORRA_CONFIG_TEMPLATE.read_text(encoding="utf-8")
        # Пример должен быть закомментирован целиком: активная секция увела бы
        # свежий контур на чужой адрес прокси.
        for line in text.splitlines():
            if "base_url" in line or "key_env" in line:
                assert line.lstrip().startswith("#"), line

    def test_template_turns_on_profile_tabs(self, template):
        """Вкладки агентов в панели без мультиплекса не работают."""
        assert template["gateway"]["multiplex_profiles"] is True

    def test_template_is_short_and_russian(self):
        """Шаблон читает владелец контура, а не разработчик апстрима."""
        text = KORRA_CONFIG_TEMPLATE.read_text(encoding="utf-8")
        assert len(text.splitlines()) < 120, "шаблон снова разросся"
        assert "hermes" not in text.lower()
        for template_path in (KORRA_CONFIG_TEMPLATE, KORRA_ENV_TEMPLATE):
            body = template_path.read_text(encoding="utf-8")
            assert any("а" <= ch <= "я" for ch in body), template_path.name

    def test_env_template_carries_no_secrets(self):
        """Шаблон .env перечисляет имена переменных, а не значения."""
        for line in KORRA_ENV_TEMPLATE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert stripped.endswith("="), f"в шаблоне не должно быть значений: {line}"


class TestStage2SeedsKorraTemplatesFirst:
    def test_hook_prefers_korra_templates(self):
        hook = STAGE2_HOOK.read_text(encoding="utf-8")
        korra_config = hook.index('seed_one "config.yaml" "korra-config.yaml.example"')
        upstream_config = hook.index('seed_one "config.yaml" "cli-config.yaml.example"')
        korra_env = hook.index('seed_one ".env" "korra-env.example"')
        upstream_env = hook.index('seed_one ".env" ".env.example"')
        # seed_one ничего не делает, если файл уже есть: значит побеждает тот,
        # кто вызван раньше. Апстримовые строки остаются запасным путём.
        assert korra_config < upstream_config
        assert korra_env < upstream_env

    def test_templates_ship_inside_the_image(self):
        """Шаблон, которого нет в образе, засевает молча ничего."""
        dockerignore = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
        ignored = {line.strip() for line in dockerignore if line.strip()}
        for name in ("korra-config.yaml.example", "korra-env.example"):
            assert name not in ignored
            assert (REPO / name).is_file()
