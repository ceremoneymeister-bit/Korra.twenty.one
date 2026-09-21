"""Служебный smoke обновления не выглядит разговором пользователя.

Приёмка 0.21.11 на канарейке: ящик «История чатов» в панели открывался строкой
«Return exact KORRA_UPDATE_OK response» — диагностический ход обновления стоял
первым среди личных разговоров. Путь дефекта:

``updater.py`` → ``POST /v1/chat/completions`` без заголовка класса разговора →
``sessions.source = 'api_server'`` → ``GET /api/sessions`` без исключений →
список чатов панели.

Правка использует уже существующий механизм маркировки: клиент объявляет класс
разговора заголовком ``X-Korra-Session-Source`` (им же панель называет себя
``dashboard``), обновление называет себя ``maintenance``, а пользовательские
списки истории прячут этот класс так же, как давно прячут ``tool``/``kanban``:
исключением по источнику, с сохранением явного запроса ``?source=…``.

Что проверяется здесь: обновление по-прежнему проверяет ACK и сохраняет
результат; служебная строка не попадает в списки разговоров; настоящая сессия
человека, написавшего ровно ``KORRA_UPDATE_OK``, остаётся видимой; служебная
сессия остаётся читаемой оператору по явному запросу.
"""

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import urllib.request
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from fastapi import FastAPI
from fastapi.testclient import TestClient as FastAPITestClient

from gateway.platforms.api_server import (
    SESSION_SOURCE_HEADER,
    APIServerAdapter,
    _derive_chat_session_id,
    _describe_provider_auth_failure,
    _ProviderAuthResolutionError,
    cors_middleware,
    security_headers_middleware,
)
from gateway.config import PlatformConfig
from gateway.session_context import clear_session_vars
from korra_cli.auth import AuthError
from korra_state import MAINTENANCE_SESSION_SOURCE, SessionDB
from korra_cli.web_routers.sessions import list_router, manage_router, search_router
import run_agent


SOURCE = Path(__file__).resolve().parents[2] / "docs/client-deploy/updater.py"
_spec = importlib.util.spec_from_file_location("client_updater", SOURCE)
u = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(u)


# ---------------------------------------------------------------------------
# Реальное исполнение smoke-кода обновления (вместо чтения его исходного
# текста). ``docs/client-deploy/updater.py`` запускает ``MODEL_SMOKE_CODE`` /
# ``FOUNDATION_SMOKE_CODE`` внутри контейнера через ``docker exec``; здесь тот
# же код исполняется настоящим ``exec`` в процессе теста, с подменённым
# ``urllib.request.urlopen`` — единственной точкой, которую скрипт использует
# для выхода в сеть — и заведомо синтетическим локальным ключом.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _patched_transport(urlopen, *, key: str, port: str):
    original_urlopen = urllib.request.urlopen
    _unset = object()
    original_key = os.environ.get("API_SERVER_KEY", _unset)
    original_port = os.environ.get("API_SERVER_PORT", _unset)
    os.environ["API_SERVER_KEY"] = key
    os.environ["API_SERVER_PORT"] = port
    urllib.request.urlopen = urlopen
    try:
        yield
    finally:
        urllib.request.urlopen = original_urlopen
        for name, original in (("API_SERVER_KEY", original_key), ("API_SERVER_PORT", original_port)):
            if original is _unset:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original


def _run_smoke_code(code: str, urlopen, *, key: str = "synthetic-maintenance-smoke-key", port: str = "0"):
    """Исполняет настоящий smoke-код обновления с подменённым транспортом."""
    with _patched_transport(urlopen, key=key, port=port):
        exec(code, {})


class _Captured(Exception):
    """Сигнал: реальный запрос собран, дальше транспорт не нужен."""

    def __init__(self, request):
        self.request = request


def _capture_request(code: str) -> "urllib.request.Request":
    """Настоящий ``Request``, который smoke-код обновления реально строит."""
    def urlopen(request, **_kwargs):
        raise _Captured(request)

    try:
        _run_smoke_code(code, urlopen)
    except _Captured as captured:
        return captured.request
    raise AssertionError("smoke-код не попытался отправить запрос")


def _header(request, name: str):
    """Значение настоящего заголовка запроса, без учёта регистра urllib."""
    lowered = {key.lower(): value for key, value in request.header_items()}
    return lowered.get(name.lower())


_MODEL_SMOKE_REQUEST = _capture_request(u.MODEL_SMOKE_CODE)
_MODEL_SMOKE_PROMPT = json.loads(_MODEL_SMOKE_REQUEST.data)["messages"][0]["content"]
_FOUNDATION_SMOKE_REQUEST = _capture_request(u.FOUNDATION_SMOKE_CODE)


def _raise_no_provider_configured(**_kwargs):
    """Тот же настоящий отказ резолвера, что ловит боевой ``_run_agent``."""
    raise _ProviderAuthResolutionError(
        _describe_provider_auth_failure(
            AuthError(
                "Провайдер ответа не настроен: добавьте ключ в разделе «Ключи».",
                code="no_provider_configured",
            )
        )
    )


# ---------------------------------------------------------------------------
# Обновление объявляет класс разговора
# ---------------------------------------------------------------------------


class TestUpdaterDeclaresTheMaintenanceClass:
    def test_model_smoke_sends_the_header_the_engine_understands(self):
        assert _header(_MODEL_SMOKE_REQUEST, SESSION_SOURCE_HEADER) == MAINTENANCE_SESSION_SOURCE

    def test_foundation_smoke_sends_it_too(self):
        """У обеих приёмок один транспорт и один класс разговора."""
        assert _header(_FOUNDATION_SMOKE_REQUEST, SESSION_SOURCE_HEADER) == MAINTENANCE_SESSION_SOURCE

    def test_model_smoke_prompt_reaching_the_engine_is_unchanged(self):
        assert _MODEL_SMOKE_PROMPT == "Reply with exactly KORRA_UPDATE_OK. Do not use tools."


# ---------------------------------------------------------------------------
# Движок принимает объявленный класс
# ---------------------------------------------------------------------------


class TestEngineAcceptsTheClass:
    def test_normalize_keeps_the_declared_value(self):
        assert (
            APIServerAdapter._normalize_session_source(MAINTENANCE_SESSION_SOURCE)
            == MAINTENANCE_SESSION_SOURCE
        )

    def test_unknown_values_still_fall_back_to_api_server(self):
        """Договор прежний: назваться можно только известным классом."""
        assert APIServerAdapter._normalize_session_source("что угодно") == "api_server"

    def test_declared_class_reaches_the_session_row(self):
        tokens = APIServerAdapter._bind_api_server_session(
            chat_id="smoke",
            session_key="smoke",
            session_id="smoke",
            session_source=MAINTENANCE_SESSION_SOURCE,
        )
        try:
            assert (
                run_agent._session_source_for_agent("api_server")
                == MAINTENANCE_SESSION_SOURCE
            )
        finally:
            clear_session_vars(tokens)

    def test_service_turn_gets_its_own_session_namespace(self):
        """Служебный ход не садится в сессию, выведенную из того же текста.

        Идентификатор stateless-хода выводится из текста запроса, поэтому до
        правки smoke пожизненно дописывался в одну и ту же строку `api-…` — ту
        самую, которую увидел клиент. Класс разговора даёт служебному ходу
        собственное пространство идентификаторов: прежняя строка больше не
        обновляется, а сторонний клиент с тем же текстом остаётся при своей.
        """
        prompt = _MODEL_SMOKE_PROMPT
        service = _derive_chat_session_id(None, prompt, source=MAINTENANCE_SESSION_SOURCE)
        user = _derive_chat_session_id(None, prompt)

        assert service != user
        assert service.startswith(f"{MAINTENANCE_SESSION_SOURCE}-")
        assert user.startswith("api-")
        # Одна установка — одна служебная строка, а не по одной на обновление.
        assert service == _derive_chat_session_id(
            None, prompt, source=MAINTENANCE_SESSION_SOURCE
        )


# ---------------------------------------------------------------------------
# Сквозной ход: HTTP-запрос обновления → строка в state.db
# ---------------------------------------------------------------------------


class _StubAgent:
    """Агент без модели, сохраняющий сессию штатным кодом ``run_agent``."""

    def __init__(self, db, session_id, reply="KORRA_UPDATE_OK"):
        self._session_db = db
        self._persist_disabled = False
        self._session_db_created = False
        self.platform = "api_server"
        self.session_id = session_id
        self.model = "stub-model"
        self._session_init_model_config = None
        self._cached_system_prompt = None
        self._parent_session_id = None
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self._reply = reply

    def run_conversation(self, user_message=None, conversation_history=None, task_id=None):
        # Именно этот метод движка решает, с каким источником родится строка.
        run_agent.AIAgent._ensure_db_session(self)
        self._session_db.append_message(self.session_id, "user", str(user_message))
        self._session_db.append_message(self.session_id, "assistant", self._reply)
        return {"final_response": self._reply, "messages": [], "api_calls": 1, "tools": []}


def _adapter_app(create_agent):
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={}))
    adapter._create_agent = create_agent
    mws = [
        mw
        for mw in (
            adapter._make_profile_prefix_middleware(),
            cors_middleware,
            security_headers_middleware,
        )
        if mw is not None
    ]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    return app


async def _post_smoke(db, headers, *, body=None, create_agent=None):
    created = {}

    def _create_agent(**kwargs):
        factory = create_agent or (lambda **kw: _StubAgent(db, kw.get("session_id")))
        agent = factory(**kwargs)
        created["agent"] = agent
        return agent

    app = _adapter_app(_create_agent)
    post_headers = dict(headers)
    post_headers.setdefault("Content-Type", "application/json")
    if body is None:
        body = json.dumps({
            "messages": [{"role": "user", "content": _MODEL_SMOKE_PROMPT}],
            "max_tokens": 24,
            "stream": False,
        }).encode()

    async with TestClient(TestServer(app)) as client:
        response = await client.post("/v1/chat/completions", data=body, headers=post_headers)
        assert response.status == 200
        payload = await response.json()
        agent = created.get("agent")
        return payload, (agent.session_id if agent else None)


async def _post_smoke_stream(db, headers, body, create_agent):
    app = _adapter_app(create_agent)
    post_headers = dict(headers)
    post_headers.setdefault("Content-Type", "application/json")

    async with TestClient(TestServer(app)) as client:
        response = await client.post("/v1/chat/completions", data=body, headers=post_headers)
        assert response.status == 200
        return await response.read()


def _run_through_engine(code, db, *, create_agent=None):
    """Исполняет реальный ``MODEL_SMOKE_CODE``, отправляя его настоящий запрос движку."""
    def urlopen(request, **_kwargs):
        payload, _sid = asyncio.run(
            _post_smoke(db, dict(request.header_items()), body=request.data, create_agent=create_agent)
        )
        return io.BytesIO(json.dumps(payload).encode())

    _run_smoke_code(code, urlopen)


def _run_foundation_through_engine(db, *, create_agent):
    """Исполняет реальный ``FOUNDATION_SMOKE_CODE`` поверх настоящего SSE движка."""
    def urlopen(request, **_kwargs):
        raw = asyncio.run(
            _post_smoke_stream(db, dict(request.header_items()), request.data, create_agent)
        )
        return io.BytesIO(raw)

    _run_smoke_code(u.FOUNDATION_SMOKE_CODE, urlopen)


@pytest.fixture
def db():
    # Тот же путь, что резолвит движок: HERMES_HOME теста задаёт conftest.
    from korra_constants import get_hermes_home

    database = SessionDB(db_path=Path(get_hermes_home()) / "state.db")
    try:
        yield database
    finally:
        database.close()


def test_declared_smoke_lands_as_a_maintenance_row(db):
    """Сквозная проверка: запрос обновления → служебная строка, ACK на месте."""
    payload, session_id = asyncio.run(
        _post_smoke(db, {SESSION_SOURCE_HEADER: MAINTENANCE_SESSION_SOURCE})
    )

    assert "KORRA_UPDATE_OK" in payload["choices"][0]["message"]["content"]
    row = db.get_session(session_id)
    assert row["source"] == MAINTENANCE_SESSION_SOURCE
    assert session_id.startswith(f"{MAINTENANCE_SESSION_SOURCE}-")


def test_undeclared_third_party_turn_is_unchanged(db):
    """Сторонний OpenAI-совместимый клиент по-прежнему остаётся ``api_server``."""
    _, session_id = asyncio.run(_post_smoke(db, {}))

    assert db.get_session(session_id)["source"] == "api_server"
    assert session_id.startswith("api-")


# ---------------------------------------------------------------------------
# Проверка ACK — поведением настоящего исполнения, а не текстом источника
# ---------------------------------------------------------------------------


class TestModelSmokeStillRequiresTheAcknowledgement:
    """Маркировка не ослабляет проверку: каждый исход — настоящее исполнение
    ``MODEL_SMOKE_CODE`` / ``FOUNDATION_SMOKE_CODE`` против настоящего движка."""

    def test_ack_success_is_a_real_engine_round_trip(self, db, capsys):
        _run_through_engine(u.MODEL_SMOKE_CODE, db)
        assert capsys.readouterr().out.strip() == "model-smoke-ok"

    def test_missing_ack_really_raises(self, db):
        def create_agent(**kwargs):
            return _StubAgent(db, kwargs.get("session_id"), reply="Здравствуйте!")

        with pytest.raises(RuntimeError, match="Model smoke response missing acknowledgement"):
            _run_through_engine(u.MODEL_SMOKE_CODE, db, create_agent=create_agent)

    def test_provider_unavailable_is_a_warning_not_a_failure(self, db, capsys):
        """Тот же настоящий отказ резолвера, что ловит боевой ``_run_agent``."""
        with pytest.raises(SystemExit) as excinfo:
            _run_through_engine(u.MODEL_SMOKE_CODE, db, create_agent=_raise_no_provider_configured)

        assert excinfo.value.code == 0
        assert capsys.readouterr().out.strip() == "model-smoke-provider-unavailable"

    def test_foundation_really_parses_the_missing_provider_sse(self, db, capsys):
        _run_foundation_through_engine(db, create_agent=_raise_no_provider_configured)
        assert capsys.readouterr().out.strip() == "foundation-smoke-ok"


# ---------------------------------------------------------------------------
# Поверхности истории
# ---------------------------------------------------------------------------


@pytest.fixture
def history_api(db):
    app = FastAPI()
    app.include_router(list_router)
    app.include_router(search_router)
    app.include_router(manage_router)
    with FastAPITestClient(app) as client:
        yield client, db


#: Идентификатор служебной строки: тот же вывод, что даёт движок на реальном
#: запросе обновления (см. test_service_turn_gets_its_own_session_namespace).
SMOKE_SESSION_ID = _derive_chat_session_id(
    None, _MODEL_SMOKE_PROMPT, MAINTENANCE_SESSION_SOURCE
)


def _chat(db, sid, *, source, title, content):
    db.create_session(sid, source=source)
    db.set_session_title(sid, title)
    db.append_message(sid, "user", content)


def _seed(db):
    """Разговоры людей, поверх которых легло обновление установки.

    Порядок как на канарейке: сначала переписка, потом обновление, поэтому
    служебная строка — самая свежая и в списке по времени стояла бы первой.
    """
    _chat(
        db,
        "personal",
        source="dashboard",
        title="Проверить подключение к Google Диску",
        content="Проверь, работает ли Google Диск",
    )
    # Человек, который сам написал контрольную строку: скрывать его нельзя —
    # совпадение текста не делает разговор служебным.
    _chat(
        db,
        "human-ack",
        source="telegram",
        title="KORRA_UPDATE_OK",
        content="Reply with exactly KORRA_UPDATE_OK. Do not use tools.",
    )
    _chat(
        db,
        SMOKE_SESSION_ID,
        source=MAINTENANCE_SESSION_SOURCE,
        title="Return exact KORRA_UPDATE_OK response",
        content="Reply with exactly KORRA_UPDATE_OK. Do not use tools.",
    )


def test_chat_history_drawer_no_longer_lists_the_smoke(history_api):
    """Ровно тот запрос, который шлёт ящик «История чатов» панели."""
    client, database = history_api
    _seed(database)

    body = client.get("/api/sessions", params={"limit": 50, "offset": 0}).json()

    assert [row["id"] for row in body["sessions"]] == ["human-ack", "personal"]
    assert body["total"] == 2


def test_history_page_categories_do_not_resurrect_it(history_api):
    """Вкладка «Чаты» шлёт свой набор исключений — служебной строки нет и там."""
    client, database = history_api
    _seed(database)

    body = client.get(
        "/api/sessions",
        params={"limit": 50, "exclude_sources": "cron,tool,api_server,acp,webhook"},
    ).json()

    assert MAINTENANCE_SESSION_SOURCE not in {row["source"] for row in body["sessions"]}


def test_search_does_not_surface_the_smoke_but_finds_the_person(history_api):
    client, database = history_api
    _seed(database)

    rows = client.get(
        "/api/sessions/search", params={"q": "KORRA_UPDATE_OK"}
    ).json()["results"]

    ids = {row["session_id"] for row in rows}
    assert "human-ack" in ids
    assert not any(row_id.startswith(MAINTENANCE_SESSION_SOURCE) for row_id in ids)


def test_operator_can_still_read_the_maintenance_session(history_api):
    """Данные не спрятаны от оператора: явный запрос класса их возвращает."""
    client, database = history_api
    _seed(database)

    listed = client.get(
        "/api/sessions", params={"source": MAINTENANCE_SESSION_SOURCE}
    ).json()
    assert [row["id"] for row in listed["sessions"]] == [SMOKE_SESSION_ID]

    detail = client.get(f"/api/sessions/{SMOKE_SESSION_ID}")
    assert detail.status_code == 200
    assert detail.json()["source"] == MAINTENANCE_SESSION_SOURCE

    messages = client.get(f"/api/sessions/{SMOKE_SESSION_ID}/messages").json()
    assert "KORRA_UPDATE_OK" in json.dumps(messages, ensure_ascii=False)


def test_session_stats_still_count_the_row(history_api):
    """Ничего не удалено: строка остаётся в учёте хранилища."""
    client, database = history_api
    _seed(database)

    stats = client.get("/api/sessions/stats").json()

    assert stats["by_source"][MAINTENANCE_SESSION_SOURCE] == 1
    assert stats["total"] == 3


# ---------------------------------------------------------------------------
# Остальные пользовательские списки
# ---------------------------------------------------------------------------


def test_cli_listing_hides_the_class_and_honours_an_explicit_source(db):
    from korra_cli.session_listing import hide_service_sources

    _seed(db)

    listed = db.list_sessions_rich(exclude_sources=hide_service_sources(None))
    assert [row["id"] for row in listed] == ["human-ack", "personal"]

    explicit = db.list_sessions_rich(
        source=MAINTENANCE_SESSION_SOURCE,
        exclude_sources=hide_service_sources(None, source=MAINTENANCE_SESSION_SOURCE),
    )
    assert [row["source"] for row in explicit] == [MAINTENANCE_SESSION_SOURCE]


def test_agent_recall_treats_it_as_a_hidden_source():
    from tools.session_search_tool import _HIDDEN_SESSION_SOURCES

    assert MAINTENANCE_SESSION_SOURCE in _HIDDEN_SESSION_SOURCES


def test_desktop_pickers_never_land_the_user_in_the_smoke(db, monkeypatch):
    """Список и авто-возврат приложения предлагают только разговоры людей."""
    import tui_gateway.server as srv
    import tui_gateway.methods_session  # noqa: F401  (регистрирует методы RPC)

    monkeypatch.setattr(srv, "_get_db", lambda: db)
    _seed(db)

    listed = srv._methods["session.list"](1, {})["result"]["sessions"]
    assert {row["id"] for row in listed} == {"human-ack", "personal"}

    # Самая свежая строка в базе — служебная; авто-возобновление обязано её
    # пропустить, иначе человек открывает приложение внутри приёмки.
    recent = srv._methods["session.most_recent"](1, {})["result"]
    assert recent["session_id"] == "human-ack"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
