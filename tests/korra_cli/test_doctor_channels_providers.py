"""Doctor называет, чем контур на самом деле распознаёт речь.

В 0.21.4 шаблон контура ставит ``stt.provider: deepgram`` и ``fallback: local``.
Без ключа контур не немеет — он молча уходит на локальный whisper из образа.
Поведение правильное, но оно прячет недонастройку: голосовые «работают», просто
каждое обрабатывается 11–13 секунд вместо 2–3. Свип 12.09.2026 нашёл восемь
таких установок из десяти, и отличить их можно было только ручным вызовом
``_get_provider()``. Значит, фактический выбор и его причину обязан называть
``doctor``.
"""

import contextlib
import io

import pytest

import korra_cli.doctor as doctor
import tools.transcription_tools as stt_tools
import tools.web_tools as web_tools


FLEET_STT = {
    "provider": "deepgram",
    "fallback": "local",
    "language": "ru",
    "providers": {
        "deepgram": {
            "command": "deepgram-transcribe {file}",
            "requires_env": ["DEEPGRAM_API_KEY"],
        },
    },
}


@pytest.fixture
def contour(monkeypatch):
    """Контур как в образе: whisper с весами на месте, конфиг — флотский."""
    monkeypatch.setattr(stt_tools, "_HAS_FASTER_WHISPER", True, raising=False)
    monkeypatch.setattr(stt_tools, "_local_stt_ready", lambda cfg=None: True)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)

    def install(stt_config):
        monkeypatch.setattr(stt_tools, "_load_stt_config", lambda: dict(stt_config))

    install(FLEET_STT)
    return install


def run(issues=None):
    """Секция целиком — как её увидит владелец."""
    issues = [] if issues is None else issues
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        doctor.check_channels_and_providers(issues)
    return output.getvalue(), issues


def run_stt(issues=None):
    """Только строка про речь: поиск и медиа проверяются отдельно."""
    issues = [] if issues is None else issues
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        doctor.check_speech_recognition(issues)
    return output.getvalue(), issues


def test_without_a_key_doctor_names_whisper_and_the_missing_variable(contour):
    text, issues = run_stt()
    assert "whisper" in text.lower()
    assert "DEEPGRAM_API_KEY" in text
    assert "deepgram" in text.lower()
    # Голосовые при этом работают, просто медленно: это недонастройка, а не
    # поломка, и в список «исправить» она не попадает.
    assert issues == []


def test_with_a_key_doctor_names_deepgram(contour, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "x" * 12)
    text, issues = run_stt()
    assert "deepgram" in text.lower()
    assert "ключ есть" in text
    assert "whisper" not in text.lower()


def test_disabled_speech_is_stated_not_guessed(contour):
    contour({**FLEET_STT, "enabled": False})
    text, issues = run_stt()
    assert "выключено" in text
    assert issues == []


def test_no_provider_at_all_is_a_failure_with_a_fix(contour, monkeypatch):
    # Ни ключа, ни локального whisper: голосовые просто не обрабатываются, и
    # это единственный случай в карточке, который обязан попасть в список
    # проблем — остальные рабочие, просто медленные.
    monkeypatch.setattr(stt_tools, "_local_stt_ready", lambda cfg=None: False)
    monkeypatch.setattr(stt_tools, "_has_local_command", lambda: False)
    monkeypatch.setattr(stt_tools, "_try_lazy_install_stt", lambda: False)
    text, issues = run_stt()
    assert "не" in text.lower()
    assert issues


def test_doctor_never_installs_packages_while_reporting(contour, monkeypatch):
    """Проверка состояния не имеет права тянуть 390 МБ колёс faster-whisper."""
    monkeypatch.setattr(stt_tools, "_local_stt_ready", lambda cfg=None: False)
    monkeypatch.setattr(stt_tools, "_has_local_command", lambda: False)

    def explode():
        raise AssertionError("doctor попытался поставить faster-whisper")

    monkeypatch.setattr(stt_tools, "_try_lazy_install_stt", explode)
    run_stt()


def test_broken_config_does_not_break_doctor(monkeypatch):
    def explode():
        raise RuntimeError("конфиг не читается")

    monkeypatch.setattr(stt_tools, "_load_stt_config", explode)
    text, issues = run()
    assert "Каналы и провайдеры" in text


# ─── Поиск в интернете (K21-065) ────────────────────────────────────────────
#
# 12.09.2026: на пяти проверенных контурах web.backend пуст, кредитная
# лестница выбирает бесключевой ddgs, модуля в образе нет, ключей поиска нет
# ни у кого. Владелец узнавал об этом единственным способом — попросив
# агента что-нибудь найти.


@pytest.fixture
def search(monkeypatch):
    """Подменяет факты, которые doctor спрашивает у самого рантайма."""
    state = {"backend": "ddgs", "available": True, "ddgs": True, "tool_ready": True}

    monkeypatch.setattr(web_tools, "_get_search_backend", lambda: state["backend"])
    monkeypatch.setattr(
        web_tools, "_is_backend_available", lambda backend: state["available"]
    )
    monkeypatch.setattr(web_tools, "_ddgs_package_importable", lambda: state["ddgs"])
    monkeypatch.setattr(web_tools, "check_web_api_key", lambda: state["tool_ready"])
    return state


def test_keyless_search_in_the_image_is_named(contour, search):
    text, issues = run()
    assert "ddgs" in text
    assert "Поиск" in text
    assert issues == []


def test_no_keyless_and_no_keyed_provider_is_a_failure(contour, search):
    search.update(backend="keenable", available=False, ddgs=False)
    text, issues = run()
    assert "Поиск" in text
    assert "не работает" in text
    assert issues and "ddgs" in issues[0]


def test_public_keyless_ring_alone_is_not_proof_of_search(contour, search):
    """Ринг объявляет себя готовым по конфигу, а не по факту.

    Ровно так контуры и жили: check_web_api_key() отвечал True, а каждый
    запрос владельца заканчивался ModuleNotFoundError.
    """
    search.update(backend="keenable", available=False, ddgs=False, tool_ready=True)
    text, issues = run()
    assert "не работает" in text
    assert issues


def test_configured_backend_down_but_bundled_keyless_left(contour, search):
    search.update(backend="exa", available=False, ddgs=True)
    text, issues = run()
    assert "exa" in text
    assert "ddgs" in text
    # Резерв в образе есть — это предупреждение, а не отказ: агент без данных
    # не остаётся.
    assert issues == []


def test_search_probe_failure_does_not_break_doctor(contour, monkeypatch):
    def explode():
        raise RuntimeError("реестр провайдеров не читается")

    monkeypatch.setattr(web_tools, "_get_search_backend", explode)
    text, _ = run()
    # Речь по-прежнему названа: одна сломавшаяся проверка не уносит секцию.
    assert "Распознавание речи" in text


# ─── Медиа из Telegram (K21-070) ────────────────────────────────────────────
#
# Успехи и провалы загрузки медиа пишутся на INFO только в файловый
# gateway.log, а в docker logs видны одни WARNING. Поэтому провал фото у
# Дмитрия пролежал девять дней: ноль успешных «Cached user …» за всю жизнь
# контура, и ни одна проверка об этом не говорила.


@pytest.fixture
def gateway_log(tmp_path, monkeypatch):
    """Журнал шлюза в подставном HERMES_HOME."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(doctor, "HERMES_HOME", tmp_path)

    def write(lines):
        (log_dir / "gateway.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return write


def _stamp(days_ago):
    from datetime import datetime, timedelta

    moment = datetime.now() - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%d %H:%M:%S,000")


def run_media(issues=None):
    issues = [] if issues is None else issues
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        doctor.check_media_intake(issues)
    return output.getvalue(), issues


def test_only_failures_is_the_nine_day_blind_spot(gateway_log):
    gateway_log([
        f"{_stamp(1)} WARNING gateway.telegram: [Telegram] Failed to cache photo: Not Found",
        f"{_stamp(2)} WARNING gateway.telegram: [Telegram] Failed to cache voice: Not Found",
    ])
    text, issues = run_media()
    assert "Медиа" in text
    assert "2" in text
    assert issues


def test_successful_intake_is_reported_green(gateway_log):
    gateway_log([
        f"{_stamp(1)} INFO gateway.telegram: [Telegram] Cached user voice at /opt/data/x.oga",
        f"{_stamp(3)} INFO gateway.telegram: [Telegram] Cached user photo at /opt/data/y.jpg",
    ])
    text, issues = run_media()
    assert "Медиа" in text
    assert issues == []


def test_partial_failures_are_visible_but_not_a_stop(gateway_log):
    gateway_log([
        f"{_stamp(1)} INFO gateway.telegram: [Telegram] Cached user voice at /opt/data/x.oga",
        f"{_stamp(1)} WARNING gateway.telegram: [Telegram] Failed to cache video: Not Found",
    ])
    text, issues = run_media()
    assert "1" in text
    assert issues == []


def test_older_than_the_window_is_not_counted(gateway_log):
    gateway_log([
        f"{_stamp(30)} WARNING gateway.telegram: [Telegram] Failed to cache photo: Not Found",
    ])
    text, issues = run_media()
    assert issues == []
    assert "не было" in text


def test_missing_log_says_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "HERMES_HOME", tmp_path)
    text, issues = run_media()
    assert text.strip() == ""
    assert issues == []
