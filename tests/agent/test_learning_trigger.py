"""K21-230: the background learning review starts on events, not counters."""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent import background_review as br
from agent import learning_trigger as lt
from run_agent import AIAgent
from tools.memory_tool import MemoryStore


def _tool_defs(*names):
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": f"{n} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for n in names
    ]


def _response(content=None, tool_calls=None, finish_reason="stop"):
    message = SimpleNamespace(
        content=content,
        reasoning_content=None,
        reasoning=None,
        reasoning_details=None,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        model="test/model",
        usage=None,
    )


def _tool_call(call_id):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name="web_search", arguments="{}"),
    )


def _make_agent(skip_memory=True):
    with (
        patch("run_agent.get_tool_definitions",
              return_value=_tool_defs("web_search", "memory", "skill_manage")),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=skip_memory,
        )
    agent.client = MagicMock()
    agent._cached_system_prompt = "You are helpful."
    agent._use_prompt_caching = False
    agent.compression_enabled = False
    agent.save_trajectories = False
    agent._memory_store = MemoryStore(user_profile_enabled=False)
    return agent


@pytest.fixture
def agent():
    return _make_agent()


def _run_turn(agent, history, text, tool_calls=0, answer="Готово."):
    """Run one real user turn through run_conversation with a scripted model."""
    script = []
    for i in range(tool_calls):
        script.append(_response(tool_calls=[_tool_call(f"c{len(history)}_{i}")],
                                finish_reason="tool_calls"))
    script.append(_response(content=answer))
    agent.client.chat.completions.create.side_effect = script
    with (
        patch("run_agent.handle_function_call", return_value="ok"),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation(text, conversation_history=history)
    return result["messages"]


# --- detector ---------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Запомни, что отчёты я получаю по понедельникам",
    "впредь отвечай короче",
    "В следующий раз сначала спроси про бюджет",
    "Всегда используй метрическую систему",
    "Больше не пиши списками",
    "Никогда не отправляй письма без моего подтверждения",
    "Remember that I prefer tabs",
    "From now on answer in English",
    "Next time ask me first",
    "always use the staging server",
])
def test_remember_requests_are_detected(text):
    assert lt.detect_learning_signal(text) == lt.SIGNAL_REMEMBER


@pytest.mark.parametrize("text", [
    "Это не так, курс был другой",
    "Неправильно, нужен PDF",
    "Надо было сначала сверить цифры",
    "Исправь это, пожалуйста",
    "That's not right, the invoice is for May",
    "You should have asked first",
])
def test_corrections_are_detected(text):
    assert lt.detect_learning_signal(text) == lt.SIGNAL_CORRECTION


@pytest.mark.parametrize("text", [
    "Найди три отеля в Сочи",
    "Всегда ли работает этот вариант?",
    "Больше не работает вход, посмотри логи",
    "Исправь баг в функции parse",
    "/refine",
    "/memory запомни",
    "Спасибо, отлично",
    "",
])
def test_ordinary_requests_are_not_events(text):
    assert lt.detect_learning_signal(text) is None


def test_correction_needs_an_earlier_answer_and_a_short_message():
    assert lt.detect_learning_signal("Неправильно, нужен PDF", has_prior_answer=False) is None
    assert lt.detect_learning_signal("неправильно " + "слово " * 200) is None


# --- 1. a long tool run is not an event --------------------------------------

def test_thirty_tool_turns_without_rules_never_start_a_review(agent):
    spawned = []
    agent._spawn_background_review = lambda **kw: spawned.append(kw) or True
    history = []
    for i in range(32):
        history = _run_turn(agent, history, f"Найди данные по пункту {i}", tool_calls=2)
    assert spawned == []
    assert agent._turns_since_memory == 0 and agent._iters_since_skill == 0


def test_one_correction_starts_exactly_one_review(agent):
    spawned = []
    agent._spawn_background_review = lambda **kw: spawned.append(kw) or True
    history = _run_turn(agent, [], "Составь список поставщиков", tool_calls=1)
    history = _run_turn(agent, history, "Это неправильно, нужен только российский список")
    assert len(spawned) == 1
    assert spawned[0]["trigger"] == lt.SIGNAL_CORRECTION
    assert spawned[0]["review_memory"] and spawned[0]["review_skills"]
    history = _run_turn(agent, history, "Спасибо")
    assert len(spawned) == 1


def test_remember_request_starts_one_review(agent):
    spawned = []
    agent._spawn_background_review = lambda **kw: spawned.append(kw) or True
    _run_turn(agent, [], "Запомни: отчёты присылай в PDF")
    assert [kw["trigger"] for kw in spawned] == [lt.SIGNAL_REMEMBER]


def test_review_is_skipped_when_the_agent_already_saved_in_this_turn(agent):
    spawned = []
    agent._spawn_background_review = lambda **kw: spawned.append(kw) or True
    messages = [
        {"role": "user", "content": "Запомни: PDF"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "m1", "type": "function", "function": {"name": "memory", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "m1", "content": "{}"},
        {"role": "assistant", "content": "Запомнила."},
    ]
    agent._learning_signal = lt.SIGNAL_REMEMBER
    assert lt.fold_learning_signal(agent, messages, False, False) == (False, False, None)
    assert agent._learning_events[-1]["reason"] == "already_saved"


def test_synthetic_user_message_is_not_an_event(agent):
    lt.note_user_turn(agent, [{"role": "user", "content": "x"}], "Запомни PDF", synthetic=True)
    assert agent._learning_signal is None


# --- 6. owner settings ------------------------------------------------------

def test_config_without_new_keys_starts_in_event_mode(agent):
    assert agent._memory_nudge_event is True and agent._skill_nudge_event is True
    assert agent._memory_nudge_interval == 0 and agent._skill_nudge_interval == 0


def test_explicit_intervals_keep_counter_behaviour(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(
        "memory:\n  nudge_interval: 3\nskills:\n  creation_nudge_interval: 0\n"
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    agent = _make_agent(skip_memory=False)
    assert agent._memory_nudge_event is False and agent._memory_nudge_interval == 3
    assert agent._skill_nudge_event is False and agent._skill_nudge_interval == 0
    spawned = []
    agent._spawn_background_review = lambda **kw: spawned.append(kw) or True
    history = []
    for i in range(3):
        history = _run_turn(agent, history, f"Вопрос {i}")
    assert len(spawned) == 1 and spawned[0]["trigger"] == lt.SIGNAL_COUNTER
    assert spawned[0]["review_skills"] is False
    # An event signal is ignored for a part the owner configured explicitly.
    _run_turn(agent, history, "Запомни: PDF")
    assert len(spawned) == 1


def test_disabled_review_ignores_event_but_manual_refine_still_runs(agent):
    with patch("agent.background_review.load_background_review_settings",
               return_value=(False, {})), \
         patch("agent.background_review.spawn_background_review_thread",
               return_value=(lambda: None, "p")) as spawn, \
         patch("run_agent.threading.Thread"):
        assert agent._spawn_background_review(
            [{"role": "user", "content": "Запомни PDF"}], True, True,
            trigger=lt.SIGNAL_REMEMBER) is False
        spawn.assert_not_called()
        assert agent._spawn_background_review(
            [{"role": "user", "content": "x"}], True, True, manual=True) is True
        spawn.assert_called_once()


def test_codex_app_server_path_uses_the_same_event_fold(agent):
    agent._learning_signal = lt.SIGNAL_CORRECTION
    messages = [
        {"role": "user", "content": "Составь отчёт"},
        {"role": "assistant", "content": "Вот отчёт."},
        {"role": "user", "content": "Неправильно, нужен PDF"},
        {"role": "assistant", "content": "Исправила."},
    ]
    assert lt.fold_learning_signal(agent, messages, False, False) == (
        True, True, lt.SIGNAL_CORRECTION)
    assert agent._learning_signal is None


# --- 2. bounded input, small iteration cap, pause ----------------------------

class _ImmediateThread:
    def __init__(self, *, target, daemon=None, name=None):
        self._target = target

    def start(self):
        self._target()


def _bare():
    from tests.run_agent.test_background_review import _bare_agent

    agent = _bare_agent()
    agent.valid_tool_names = {"memory", "skill_manage"}
    return agent


class _FakeFork:
    """Stands in for the cache-parity fork; records what the review feeds it."""

    def __init__(self, writes=False):
        self.calls = []
        self._session_messages = []
        self._memory_enabled = True
        self._user_profile_enabled = False
        self.writes = writes
        self.close = lambda: None
        self.shutdown_memory_provider = lambda: None

    def run_conversation(self, **kwargs):
        self.calls.append(kwargs)
        if self.writes:
            self._session_messages = [{
                "role": "tool", "tool_call_id": "n",
                "content": '{"success": true, "message": "Entry added", "target": "memory"}',
            }]


def _long_history():
    msgs = []
    for i in range(60):
        msgs.append({"role": "user", "content": f"EARLY-{i} " + "x" * 400})
        msgs.append({"role": "assistant", "content": f"early answer {i} " + "y" * 400})
    msgs += [
        {"role": "user", "content": "Составь прайс поставщиков"},
        {"role": "assistant", "content": "Вот прайс: цены в долларах."},
        {"role": "user", "content": "Неправильно, нужны рубли"},
        {"role": "assistant", "content": "Пересчитала в рубли."},
    ]
    return msgs


def _spawn(agent, fork, messages, trigger, memory=True, skills=True):
    with patch("agent.background_review.load_background_review_settings",
               return_value=(True, {})), \
         patch("agent.background_review.build_cache_parity_fork",
               return_value=(fork, {}, False)) as build, \
         patch("run_agent.threading.Thread", _ImmediateThread):
        started = agent._spawn_background_review(messages, memory, skills, trigger=trigger)
    return started, build


def test_event_review_gets_a_bounded_excerpt_and_a_small_iteration_cap():
    agent, fork = _bare(), _FakeFork()
    started, build = _spawn(agent, fork, _long_history(), lt.SIGNAL_CORRECTION)
    assert started is True
    assert build.call_count == 1 and len(fork.calls) == 1
    assert build.call_args.kwargs["max_iterations"] == lt.EVENT_MAX_ITERATIONS < 16
    call = fork.calls[0]
    assert call["conversation_history"] == []
    prompt = call["user_message"]
    assert "Неправильно, нужны рубли" in prompt and "Вот прайс" in prompt
    assert "EARLY-" not in prompt
    assert len(prompt) < lt.EXCERPT_MAX_CHARS + 6000
    assert br._BASIS_QUALITY_BLOCK in prompt


def test_manual_refine_keeps_full_history_and_full_iteration_cap():
    agent, fork = _bare(), _FakeFork()
    history = _long_history()
    with patch("agent.background_review.load_background_review_settings",
               return_value=(False, {})), \
         patch("agent.background_review.build_cache_parity_fork",
               return_value=(fork, {}, False)) as build, \
         patch("run_agent.threading.Thread", _ImmediateThread):
        assert agent._spawn_background_review(history, True, True, manual=True) is True
    assert build.call_args.kwargs["max_iterations"] == br._REVIEW_MAX_ITERATIONS
    assert len(fork.calls[0]["conversation_history"]) == len(history)


def test_two_empty_reviews_pause_corrections_until_an_explicit_signal(caplog):
    agent = _bare()
    history = _long_history()
    caplog.set_level(logging.INFO)
    for _ in range(2):
        started, _build = _spawn(agent, _FakeFork(), history, lt.SIGNAL_CORRECTION)
        assert started is True
    assert agent._learning_empty_streak == 2
    fork = _FakeFork()
    started, build = _spawn(agent, fork, history, lt.SIGNAL_CORRECTION)
    assert started is False and build.call_count == 0 and fork.calls == []
    assert agent._learning_events[-1]["reason"] == "paused_after_empty_reviews"
    assert "paused_after_empty_reviews" in caplog.text
    # An explicit "remember" request still runs and a write resets the pause.
    started, _build = _spawn(agent, _FakeFork(writes=True), history, lt.SIGNAL_REMEMBER)
    assert started is True and agent._learning_empty_streak == 0
    started, _build = _spawn(agent, _FakeFork(), history, lt.SIGNAL_CORRECTION)
    assert started is True


def test_manual_refine_lifts_the_pause():
    agent = _bare()
    agent._learning_empty_streak = 5
    fork = _FakeFork()
    with patch("agent.background_review.load_background_review_settings",
               return_value=(False, {})), \
         patch("agent.background_review.build_cache_parity_fork",
               return_value=(fork, {}, False)), \
         patch("run_agent.threading.Thread", _ImmediateThread):
        assert agent._spawn_background_review(_long_history(), True, True, manual=True) is True
    assert agent._learning_empty_streak == 0 and len(fork.calls) == 1


# --- 3. full memory never reaches the model -----------------------------------

def _full_store(tmp_path):
    store = MemoryStore(memory_char_limit=200, user_char_limit=200,
                        user_profile_enabled=False)
    store.load_from_disk()
    store.add("memory", "x" * 190)
    return store


def test_full_memory_does_not_call_the_model_and_is_logged(tmp_path, caplog):
    agent, fork = _bare(), _FakeFork()
    agent._memory_store = _full_store(tmp_path)
    caplog.set_level(logging.INFO)
    started, build = _spawn(agent, fork, _long_history(), lt.SIGNAL_REMEMBER,
                            memory=True, skills=False)
    assert started is False
    assert build.call_count == 0 and fork.calls == []
    assert agent._learning_events[-1]["reason"] == "memory_full"
    assert "memory_full" in caplog.text


def test_full_memory_still_lets_the_skills_part_run():
    agent, fork = _bare(), _FakeFork()
    agent._memory_store = _full_store(None)
    started, build = _spawn(agent, fork, _long_history(), lt.SIGNAL_REMEMBER)
    assert started is True and build.call_count == 1
    assert "**Memory**" not in fork.calls[0]["user_message"]
    assert "**Skills**" in fork.calls[0]["user_message"]


def test_memory_with_room_is_reviewed():
    agent, fork = _bare(), _FakeFork()
    agent._memory_store = MemoryStore(memory_char_limit=2200, user_profile_enabled=False)
    started, _build = _spawn(agent, fork, _long_history(), lt.SIGNAL_REMEMBER,
                             memory=True, skills=False)
    assert started is True and "**Memory**" in fork.calls[0]["user_message"]


# --- 5. pre_background_review hook --------------------------------------------

def test_hook_is_registered_and_documented_as_a_contract():
    from pathlib import Path

    from korra_cli import plugins

    assert "pre_background_review" in plugins.VALID_HOOKS
    assert "pre_background_review" in plugins._HOOK_TIMEOUT_BOUNDED_HOOKS
    assert '{"skip": True' in Path(plugins.__file__).read_text(encoding="utf-8")


def test_plugin_skip_makes_zero_model_calls_and_is_visible(caplog):
    agent, fork = _bare(), _FakeFork()
    caplog.set_level(logging.INFO)
    seen = {}

    def fake_invoke(name, **kwargs):
        seen.update(name=name, **kwargs)
        return [None, {"skip": True, "reason": "classifier: not a lesson"}]

    with patch("korra_cli.lifecycle.invoke_hook", side_effect=fake_invoke):
        started, build = _spawn(agent, fork, _long_history(), lt.SIGNAL_CORRECTION)
    assert started is True  # the thread started; the hook vetoed it before any model call
    assert build.call_count == 0 and fork.calls == []
    assert seen["name"] == "pre_background_review"
    assert seen["event_kind"] == lt.SIGNAL_CORRECTION
    assert "Неправильно, нужны рубли" in seen["excerpt"] and "EARLY-" not in seen["excerpt"]
    assert seen["session_id"] == "test-session" and seen["profile"]
    last = agent._learning_events[-1]
    assert last["outcome"] == "skipped" and "classifier: not a lesson" in last["reason"]
    assert "classifier: not a lesson" in caplog.text


@pytest.mark.parametrize("answer", [None, [], [None], [{"skip": False}], [{"skip": "yes"}], ["skip"]])
def test_plugin_without_skip_lets_the_review_run(answer):
    agent, fork = _bare(), _FakeFork()
    with patch("korra_cli.lifecycle.invoke_hook", return_value=answer):
        _spawn(agent, fork, _long_history(), lt.SIGNAL_CORRECTION)
    assert len(fork.calls) == 1


def test_plugin_exception_is_fail_open():
    agent, fork = _bare(), _FakeFork()
    with patch("korra_cli.lifecycle.invoke_hook", side_effect=RuntimeError("boom")):
        _spawn(agent, fork, _long_history(), lt.SIGNAL_CORRECTION)
    assert len(fork.calls) == 1


def test_real_plugin_callback_can_skip_and_a_raising_one_cannot_block(tmp_path, monkeypatch):
    from korra_cli.plugins import PluginManager

    from tests.korra_cli.test_plugins import _make_plugin_dir

    plugins_dir = tmp_path / "hermes_test" / "plugins"
    _make_plugin_dir(
        plugins_dir, "skipper",
        register_body=(
            'ctx.register_hook("pre_background_review", '
            'lambda **kw: {"skip": True, "reason": "plugin says no"})'
        ),
    )
    _make_plugin_dir(
        plugins_dir, "broken",
        register_body=(
            'ctx.register_hook("pre_background_review", '
            'lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))'
        ),
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes_test"))
    mgr = PluginManager()
    mgr.discover_and_load()
    results = mgr.invoke_hook("pre_background_review", event_kind="correction",
                              excerpt="e", profile="default", session_id="s")
    assert results == [{"skip": True, "reason": "plugin says no"}]


# --- 6b/6d: findings -------------------------------------------------------

def test_review_input_budget_counts_cache_reads():
    from agent.conversation_loop import _review_input_budget_exhausted

    agent = SimpleNamespace(
        _review_input_token_budget=1000,
        session_input_tokens=10,
        session_cache_read_tokens=5000,
        session_cache_write_tokens=0,
    )
    assert _review_input_budget_exhausted(agent) is True
    agent.session_cache_read_tokens = 100
    assert _review_input_budget_exhausted(agent) is False


def test_completion_line_has_outcome_trigger_and_billing_status(caplog):
    caplog.set_level(logging.INFO, logger="agent.background_review")
    agent, fork = _bare(), _FakeFork(writes=True)
    fork.session_cost_status = "included"
    with patch("agent.background_review._snapshot_review_usage",
               return_value={"api_calls": 1, "input_tokens": 9, "output_tokens": 3,
                             "cache_read_tokens": 7, "cache_write_tokens": 2,
                             "cost_status": "included"}):
        _spawn(agent, fork, _long_history(), lt.SIGNAL_REMEMBER)
    line = next(r.message for r in caplog.records if "Background review complete" in r.message)
    assert "trigger=remember" in line
    assert "result=memory" in line
    assert "cost_status=included" in line
    assert "cache_write=2" in line


def test_every_review_prompt_carries_the_basis_quality_rules():
    for prompt in (br._MEMORY_REVIEW_PROMPT, br._SKILL_REVIEW_PROMPT,
                   br._COMBINED_REVIEW_PROMPT,
                   br.build_event_review_prompt("correction", True, True, "EXCERPT")):
        assert "Quality of the basis" in prompt
        assert "is NOT a basis" in prompt
        assert "one-off exception" in prompt
