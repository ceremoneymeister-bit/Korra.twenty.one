"""Korra: распознавание речи из коробки — Deepgram по ключу, иначе локальный whisper.

Решение владельца 04.09.2026: в образе Korra 21 распознавание должно работать
сразу, без единого ключа. Ключ Deepgram остаётся вариантом, но его отсутствие
больше не означает «речи нет»: движок уходит на локальный whisper, вшитый в
образ вместе с весами.

У апстрима такой механики нет — ``stt.provider`` там жёсткий выбор без цепочки,
и командный провайдер узнаёт об отсутствии ключа уже внутри запущенной команды,
когда выбрать другой провайдер поздно. Форк добавляет две объявляемые конфигом
вещи и проверяет здесь обе:

* ``stt.<провайдер>.requires_env`` — какие переменные окружения нужны провайдеру,
  чтобы считаться готовым (проверяется ДО запуска команды);
* ``stt.fallback`` — куда уходить, когда выбранный провайдер не готов.

Плюс путь к весам: имя размера модели превращается в каталог, вшитый в образ,
чтобы в рантайме ничего не качалось.
"""

from unittest.mock import patch

import pytest

from tools import transcription_tools as tt


DEEPGRAM_SECTION = {
    "type": "command",
    "command": 'curl -sS https://api.deepgram.com/v1/listen --data-binary @{input_path}',
    "requires_env": ["DEEPGRAM_API_KEY"],
    "env_passthrough": ["DEEPGRAM_API_KEY"],
}


def _config(**overrides):
    """Конфиг контура: Deepgram по ключу, локальный whisper как запасной."""
    cfg = {
        "provider": "deepgram",
        "fallback": "local",
        "language": "ru",
        "deepgram": dict(DEEPGRAM_SECTION),
    }
    cfg.update(overrides)
    return cfg


def _env(**values):
    """Подменить чтение окружения движком (config > .env > os.environ)."""
    return patch.object(tt, "get_env_value", lambda name, default=None: values.get(name, default))


@pytest.fixture(autouse=True)
def _isolated_models_dir(tmp_path, monkeypatch):
    """Каталог вшитых весов всегда указывает в пустую песочницу.

    Иначе тесты зависели бы от того, есть ли на машине прогона настоящий
    /opt/hermes/models/whisper: на сборочном хосте «весов нет», в контейнере
    образа «веса есть», и один и тот же тест давал бы разные ответы.
    """
    root = tmp_path / "baked-models"
    root.mkdir()
    monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(root))
    return root


@pytest.fixture
def baked_whisper(_isolated_models_dir):
    """Веса medium, вшитые в образ, как после сборки."""
    model_dir = _isolated_models_dir / "medium"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"ct2")
    return model_dir


def _fake_whisper_module(recorder):
    """Подставной ``faster_whisper``: записывает, с чем позвали WhisperModel.

    Именно модуль, а не patch по атрибуту: patch("faster_whisper.WhisperModel")
    сначала импортирует настоящий пакет, а его в тестовом окружении нет, и
    повторный импорт numpy в одном процессе падает.
    """
    import types

    module = types.ModuleType("faster_whisper")

    class FakeWhisperModel:
        def __init__(self, model_name, **kwargs):
            recorder["model_name"] = model_name
            recorder["kwargs"] = kwargs

    module.WhisperModel = FakeWhisperModel
    return module


def _raw_selection(provider="deepgram"):
    """Сырой config.yaml: выбор провайдера записан человеком, а не пришёл из дефолтов."""
    return patch(
        "hermes_cli.config.read_raw_config_readonly",
        return_value={"stt": {"provider": provider}},
    )


# ---------------------------------------------------------------------------
# requires_env — готовность провайдера объявляется, а не выясняется по факту
# ---------------------------------------------------------------------------


class TestRequiresEnv:
    def test_missing_key_is_reported(self):
        with _env():
            assert tt._stt_missing_required_env("deepgram", _config()) == ["DEEPGRAM_API_KEY"]

    def test_present_key_is_not_reported(self):
        with _env(DEEPGRAM_API_KEY="dg-live-key"):
            assert tt._stt_missing_required_env("deepgram", _config()) == []

    def test_blank_value_counts_as_missing(self):
        """Пустая строка в .env — это «ключа нет», а не «ключ есть и пустой»."""
        with _env(DEEPGRAM_API_KEY="   "):
            assert tt._stt_missing_required_env("deepgram", _config()) == ["DEEPGRAM_API_KEY"]

    def test_requires_env_accepts_a_bare_string(self):
        cfg = _config(deepgram=dict(DEEPGRAM_SECTION, requires_env="DEEPGRAM_API_KEY"))
        with _env():
            assert tt._stt_missing_required_env("deepgram", cfg) == ["DEEPGRAM_API_KEY"]

    def test_without_requires_env_provider_is_always_ready(self):
        """Поведение апстрима: не объявил требований — значит требований нет."""
        section = dict(DEEPGRAM_SECTION)
        section.pop("requires_env")
        with _env():
            assert tt._stt_missing_required_env("deepgram", _config(deepgram=section)) == []

    def test_canonical_providers_layout_is_honoured(self):
        """``stt.providers.<имя>`` — каноническое место, ``stt.<имя>`` — совместимость."""
        cfg = {"provider": "deepgram", "providers": {"deepgram": dict(DEEPGRAM_SECTION)}}
        with _env():
            assert tt._stt_missing_required_env("deepgram", cfg) == ["DEEPGRAM_API_KEY"]


class TestFallbackCandidates:
    def test_string_becomes_single_candidate(self):
        assert tt._stt_fallback_candidates({"fallback": "local"}) == ["local"]

    def test_list_keeps_order(self):
        assert tt._stt_fallback_candidates(
            {"fallback": ["local_command", "local"]}
        ) == ["local_command", "local"]

    def test_absent_key_means_no_chain(self):
        assert tt._stt_fallback_candidates({}) == []


# ---------------------------------------------------------------------------
# Выбор провайдера
# ---------------------------------------------------------------------------


class TestProviderSelection:
    def test_key_present_keeps_deepgram(self):
        with _env(DEEPGRAM_API_KEY="dg-live-key"), _raw_selection():
            assert tt._get_provider(_config()) == "deepgram"

    def test_no_key_falls_back_to_local_whisper(self, baked_whisper):
        """Главный сценарий владельца: ключа нет, речь всё равно распознаётся."""
        with _env(), _raw_selection(), patch.object(tt, "_HAS_FASTER_WHISPER", True):
            assert tt._get_provider(_config()) == "local"

    def test_without_fallback_key_upstream_refusal_is_preserved(self, baked_whisper):
        """Без ``stt.fallback`` форк ведёт себя как апстрим — молча не подменяет выбор."""
        cfg = _config()
        cfg.pop("fallback")
        with _env(), _raw_selection(), patch.object(tt, "_HAS_FASTER_WHISPER", True):
            assert tt._get_provider(cfg) == "deepgram"

    def test_unavailable_fallback_returns_the_original_choice(self):
        """Локального whisper в сборке нет: вернуть deepgram, чтобы человек увидел
        его собственную ошибку про ключ, а не общее «ни одного провайдера»."""
        with _env(), _raw_selection(), \
             patch.object(tt, "_HAS_FASTER_WHISPER", False), \
             patch.object(tt, "_has_local_command", return_value=False), \
             patch.object(tt, "_try_lazy_install_stt", return_value=False):
            assert tt._get_provider(_config()) == "deepgram"

    def test_chain_walks_to_the_first_ready_candidate(self, baked_whisper):
        cfg = _config(fallback=["groq", "local"])
        with _env(), _raw_selection(), \
             patch.object(tt, "_HAS_FASTER_WHISPER", True), \
             patch.object(tt, "_resolve_provider_key", return_value=""):
            assert tt._get_provider(cfg) == "local"

    def test_disabled_stt_stays_disabled(self, baked_whisper):
        """``stt.enabled: false`` — это выключено, а не «поищи запасной»."""
        with _env(), _raw_selection(), patch.object(tt, "_HAS_FASTER_WHISPER", True):
            assert tt._get_provider(_config(enabled=False)) == "none"

    def test_builtin_provider_without_key_also_uses_the_chain(self, baked_whisper):
        """Цепочка не привязана к Deepgram: встроенный провайдер без ключа тоже уходит."""
        cfg = {"provider": "groq", "fallback": "local"}
        with _env(), _raw_selection("groq"), \
             patch.object(tt, "_HAS_FASTER_WHISPER", True), \
             patch.object(tt, "_resolve_provider_key", return_value=""):
            assert tt._get_provider(cfg) == "local"


# ---------------------------------------------------------------------------
# Веса модели: вшиты в образ, в рантайме не качаются
# ---------------------------------------------------------------------------


class TestBakedModelDir:
    def _baked(self, tmp_path, size="medium"):
        model_dir = tmp_path / size
        model_dir.mkdir(parents=True)
        (model_dir / "model.bin").write_bytes(b"ct2")
        return model_dir

    def test_size_name_resolves_to_the_baked_directory(self, tmp_path, monkeypatch):
        model_dir = self._baked(tmp_path)
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        assert tt._resolve_local_model_source("medium") == str(model_dir)

    def test_absent_directory_keeps_the_size_name(self, tmp_path, monkeypatch):
        """Резолвер только выбирает источник, запрещать не его работа."""
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        assert tt._resolve_local_model_source("medium") == "medium"

    def test_directory_without_weights_is_not_used(self, tmp_path, monkeypatch):
        (tmp_path / "medium").mkdir()
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        assert tt._resolve_local_model_source("medium") == "medium"

    def test_baked_size_wins_over_the_configured_one(self, tmp_path, monkeypatch):
        """Образ собран с --build-arg WHISPER_MODEL_SIZE=small, конфиг просит
        medium: берём то, что реально вшито, иначе сборка отказала бы молча."""
        small = self._baked(tmp_path, "small")
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        assert tt._resolve_local_model_source("medium") == str(small)

    def test_ambiguous_bake_does_not_guess(self, tmp_path, monkeypatch):
        """Вшито две модели, ни одна не совпала с конфигом: угадывать нечего."""
        self._baked(tmp_path, "small")
        self._baked(tmp_path, "large-v3")
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        assert tt._resolve_local_model_source("medium") == "medium"

    def test_configured_size_wins_when_it_is_baked(self, tmp_path, monkeypatch):
        medium = self._baked(tmp_path, "medium")
        self._baked(tmp_path, "small")
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        assert tt._resolve_local_model_source("medium") == str(medium)

    def test_explicit_path_is_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        assert tt._resolve_local_model_source("/models/my-whisper") == "/models/my-whisper"

    def test_huggingface_repo_id_is_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        assert (
            tt._resolve_local_model_source("Systran/faster-whisper-medium")
            == "Systran/faster-whisper-medium"
        )

    def test_loader_passes_the_baked_path_to_whisper(self, tmp_path, monkeypatch):
        """Сквозная проверка: в WhisperModel уезжает каталог, а не имя размера."""
        model_dir = self._baked(tmp_path)
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        calls = {}

        with patch.dict("sys.modules", {"faster_whisper": _fake_whisper_module(calls)}), \
             patch.object(tt, "_should_force_faster_whisper_cpu", return_value=False):
            tt._load_local_whisper_model(
                "medium", device="cpu", compute_type="int8", cpu_threads=4,
            )

        assert calls["model_name"] == str(model_dir)
        assert calls["kwargs"] == {
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 4,
            # Сеть закрыта на настоящей границе — параметром самой библиотеки.
            "local_files_only": True,
        }

    def test_permission_to_download_opens_the_network(self, tmp_path, monkeypatch):
        """``stt.local.allow_download: true`` снимает local_files_only."""
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        calls = {}

        with patch.dict("sys.modules", {"faster_whisper": _fake_whisper_module(calls)}), \
             patch.object(tt, "_should_force_faster_whisper_cpu", return_value=False):
            tt._load_local_whisper_model("medium", allow_download=True)

        assert calls["model_name"] == "medium"
        assert calls["kwargs"]["local_files_only"] is False

    def test_network_is_closed_by_default(self, tmp_path, monkeypatch):
        """Без разрешения библиотека обязана получить local_files_only=True.

        Это и есть запрет на скачивание: он стоит там, где сеть открывается,
        и его нельзя обойти мимо резолвера.
        """
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        calls = {}

        with patch.dict("sys.modules", {"faster_whisper": _fake_whisper_module(calls)}), \
             patch.object(tt, "_should_force_faster_whisper_cpu", return_value=False):
            tt._load_local_whisper_model("medium")

        assert calls["kwargs"]["local_files_only"] is True


class TestLocalReadinessGate:
    """Без весов локальный провайдер выбирать нельзя."""

    def _cfg(self, tmp_path, **local):
        return _config(local=dict({"model": "medium"}, **local)), tmp_path

    def test_missing_weights_make_local_unready(self, tmp_path, monkeypatch):
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        cfg = _config(local={"model": "medium"})
        with patch.object(tt, "_HAS_FASTER_WHISPER", True):
            assert tt._local_stt_ready(cfg) is False

    def test_baked_weights_make_local_ready(self, tmp_path, monkeypatch):
        (tmp_path / "medium").mkdir()
        (tmp_path / "medium" / "model.bin").write_bytes(b"ct2")
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        cfg = _config(local={"model": "medium"})
        with patch.object(tt, "_HAS_FASTER_WHISPER", True):
            assert tt._local_stt_ready(cfg) is True

    def test_chain_does_not_route_into_an_unusable_local(self, tmp_path, monkeypatch):
        """Ключа нет и весов нет: подменять Deepgram на заведомо мёртвый local
        нельзя, иначе человек получит невнятный отказ вместо «нужен ключ»."""
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        cfg = _config(local={"model": "medium"})
        with _env(), _raw_selection(), \
             patch.object(tt, "_HAS_FASTER_WHISPER", True), \
             patch.object(tt, "_has_local_command", return_value=False), \
             patch.object(tt, "_try_lazy_install_stt", return_value=False):
            assert tt._get_provider(cfg) == "deepgram"

    def test_forced_local_without_weights_explains_itself(self, tmp_path, monkeypatch):
        """Отказ библиотеки переводится в текст, по которому понятно, что делать.

        faster-whisper под local_files_only=True бросает английское исключение
        про кэш и local_files_only. Владельцу оно не говорит ничего, поэтому
        движок подменяет его своим текстом с обоими выходами.
        """
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        audio = tmp_path / "voice.wav"
        audio.write_bytes(b"RIFF")

        class LocalEntryNotFoundError(Exception):
            pass

        def refuse(*_a, **_kw):
            raise LocalEntryNotFoundError(
                "Cannot find an appropriate cached snapshot for "
                "Systran/faster-whisper-medium (local_files_only=True)"
            )

        with patch.object(tt, "_HAS_FASTER_WHISPER", True), \
             patch.object(tt, "_load_local_whisper_model", side_effect=refuse), \
             patch.object(tt, "_local_model", None), \
             patch.object(tt, "_local_model_name", None), \
             patch.object(tt, "_load_stt_config", return_value=_config(local={"model": "medium"})):
            result = tt._transcribe_local(str(audio), "medium")

        assert result["success"] is False
        assert result["error_type"] == "local_weights_missing"
        assert "DEEPGRAM_API_KEY" in result["error"]
        assert "allow_download" in result["error"]

    def test_a_real_model_failure_is_not_disguised(self, tmp_path, monkeypatch):
        """Настоящая поломка модели обязана всплыть как есть, а не как «нет весов»."""
        monkeypatch.setenv(tt.LOCAL_STT_MODELS_DIR_ENV, str(tmp_path))
        audio = tmp_path / "voice.wav"
        audio.write_bytes(b"RIFF")

        with patch.object(tt, "_HAS_FASTER_WHISPER", True), \
             patch.object(tt, "_load_local_whisper_model",
                          side_effect=RuntimeError("CUDA out of memory")), \
             patch.object(tt, "_local_model", None), \
             patch.object(tt, "_local_model_name", None), \
             patch.object(tt, "_load_stt_config", return_value=_config(local={"model": "medium"})):
            result = tt._transcribe_local(str(audio), "medium")

        assert result["success"] is False
        assert result.get("error_type") != "local_weights_missing"
        assert "CUDA out of memory" in result["error"]


class TestCommandEnvAgreesWithReadinessCheck:
    """Проверка готовности и запуск команды обязаны читать ключ одинаково.

    Ключ, дописанный в .env контура без рестарта, виден движку через
    get_env_value, но не виден в os.environ. Пока окружение дочернего процесса
    собиралось только из os.environ, проверка говорила «Deepgram готов»,
    fallback не срабатывал, а команда падала с кодом 3: распознавания не было
    вообще.
    """

    def test_key_only_in_dotenv_reaches_the_child_process(self, monkeypatch):
        """Настоящая команда, настоящее окружение: ключ виден только движку."""
        monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
        with _env(DEEPGRAM_API_KEY="dg-from-dotenv"):
            result = tt._run_command_stt(
                'printf %s "${DEEPGRAM_API_KEY:-ПУСТО}"',
                timeout=30,
                env_passthrough=["DEEPGRAM_API_KEY"],
            )
        assert result.stdout.strip() == "dg-from-dotenv", (
            "ключ из .env обязан доехать до команды: иначе проверка готовности "
            "говорит «Deepgram готов», fallback не срабатывает, а команда падает"
        )

    def test_readiness_and_child_env_agree_on_the_same_key(self, monkeypatch):
        """Оба конца читают ключ одинаково: готов ⇒ команда его увидит."""
        monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
        cfg = _config()
        with _env(DEEPGRAM_API_KEY="dg-from-dotenv"):
            assert tt._stt_missing_required_env("deepgram", cfg) == []
            result = tt._run_command_stt(
                'printf %s "${DEEPGRAM_API_KEY:-ПУСТО}"',
                timeout=30,
                env_passthrough=tt._command_stt_env_passthrough(cfg["deepgram"]),
            )
        assert result.stdout.strip() == "dg-from-dotenv"

    def test_absent_key_stays_absent_in_the_child(self):
        """Обратная сторона: чего нет, то и не подставляется."""
        with _env():
            result = tt._run_command_stt(
                'printf %s "${DEEPGRAM_API_KEY:-ПУСТО}"',
                timeout=30,
                env_passthrough=["DEEPGRAM_API_KEY"],
            )
        assert result.stdout.strip() == "ПУСТО"


class TestCpuThreads:
    def test_default_leaves_the_decision_to_ctranslate2(self):
        assert tt._get_local_cpu_threads({}) == 0

    def test_configured_value_is_used(self):
        assert tt._get_local_cpu_threads({"cpu_threads": 4}) == 4

    def test_negative_and_garbage_degrade_to_zero(self):
        assert tt._get_local_cpu_threads({"cpu_threads": -2}) == 0
        assert tt._get_local_cpu_threads({"cpu_threads": "четыре"}) == 0


# ---------------------------------------------------------------------------
# Шаблон конфига: то же правило приезжает на контур без ручной правки YAML
# ---------------------------------------------------------------------------


class TestConfigTemplate:
    def test_defaults_pair_deepgram_with_a_local_fallback(self):
        from hermes_cli.config_defaults import DEFAULT_CONFIG

        stt = DEFAULT_CONFIG["stt"]
        assert stt["provider"] == "deepgram"
        assert stt["fallback"] == "local"
        assert stt["deepgram"]["requires_env"] == ["DEEPGRAM_API_KEY"]

    def test_defaults_pin_the_medium_model_on_cpu(self):
        from hermes_cli.config_defaults import DEFAULT_CONFIG

        local = DEFAULT_CONFIG["stt"]["local"]
        assert local["model"] == "medium"
        assert local["compute_type"] == "int8"
        assert local["cpu_threads"] == 4
        assert local["allow_download"] is False

    def test_defaults_release_the_model_after_five_idle_minutes(self):
        """Medium весит около гигабайта в резиденте, и на VPS он делит память
        с самим агентом. Апстримный 0 («не выгружать никогда») писался под
        base в 150 МБ; здесь модель отпускается, а следующее голосовое платит
        полторы секунды повторной загрузки с диска образа, без сети."""
        from hermes_cli.config_defaults import DEFAULT_CONFIG
        from tools.transcription_tools import _get_idle_unload_seconds

        local = DEFAULT_CONFIG["stt"]["local"]
        assert local["unload_after_idle_seconds"] == 300
        # Значение должно быть не просто записано, а понято резолвером таймера.
        assert _get_idle_unload_seconds(local) == 300

    def test_fresh_defaults_select_deepgram_and_fall_back_without_a_key(self, baked_whisper):
        """Свежая установка без ключей: правило владельца работает как есть."""
        from hermes_cli.config_defaults import DEFAULT_CONFIG

        stt = DEFAULT_CONFIG["stt"]
        with _env(), _raw_selection(), patch.object(tt, "_HAS_FASTER_WHISPER", True):
            assert tt._get_provider(stt) == "local"
        with _env(DEEPGRAM_API_KEY="dg-live-key"), _raw_selection():
            assert tt._get_provider(stt) == "deepgram"

    def test_contour_command_survives_the_merge_and_gains_requires_env(self):
        """Контур со своей секцией ``stt.deepgram`` не теряет её и получает
        requires_env из дефолтов — правку живого config.yaml это не требует."""
        from hermes_cli.config import _deep_merge
        from hermes_cli.config_defaults import DEFAULT_CONFIG

        contour = {"stt": {"provider": "deepgram", "deepgram": {"command": "мой-скрипт {input_path}"}}}
        merged = _deep_merge(DEFAULT_CONFIG, contour)["stt"]

        assert merged["deepgram"]["command"] == "мой-скрипт {input_path}"
        assert merged["deepgram"]["requires_env"] == ["DEEPGRAM_API_KEY"]
        assert merged["fallback"] == "local"


class TestProfileSeeding:
    def test_new_profile_inherits_the_contour_stt_section(self, tmp_path):
        """Профиль — не отдельный контур: правило распознавания у него общее."""
        import yaml

        from hermes_cli.profiles import _seed_model_config

        source = tmp_path / "home"
        source.mkdir()
        (source / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "model": {"provider": "custom", "name": "dario"},
                    "stt": {"provider": "deepgram", "fallback": "local", "language": "ru"},
                },
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        profile = tmp_path / "profiles" / "secretary"
        profile.mkdir(parents=True)

        _seed_model_config(profile, source)

        seeded = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
        assert seeded["stt"] == {"provider": "deepgram", "fallback": "local", "language": "ru"}

    def test_contour_without_an_stt_section_seeds_nothing(self, tmp_path):
        """Нечего наследовать — профиль просто живёт на дефолтах движка."""
        import yaml

        from hermes_cli.profiles import _seed_model_config

        source = tmp_path / "home"
        source.mkdir()
        (source / "config.yaml").write_text(
            yaml.safe_dump({"model": {"provider": "custom", "name": "dario"}}, sort_keys=False),
            encoding="utf-8",
        )
        profile = tmp_path / "profiles" / "fresh"
        profile.mkdir(parents=True)

        _seed_model_config(profile, source)

        seeded = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
        assert "stt" not in seeded
