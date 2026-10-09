"""K21-325: the review result comes from real memory/skill writes, not summary wording."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.background_review import _classify_review_result, summarize_background_review_actions
from agent.learning_trigger import SIGNAL_CORRECTION, note_review_result, review_paused

SKILL = "review-procedure"
SKILL_BODY = "---\nname: review-procedure\ndescription: Synthetic procedure.\n---\n\nVersion zero.\n"


@pytest.fixture
def tools(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KORRA_HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    from agent import skill_utils
    from tools import memory_tool, skill_ledger, skill_manager_tool, skill_usage

    monkeypatch.setattr(skill_ledger, "get_hermes_home", lambda: home)
    monkeypatch.setattr(skill_usage, "get_hermes_home", lambda: home)
    monkeypatch.setattr(skill_manager_tool, "SKILLS_DIR", home / "skills")
    monkeypatch.setattr(skill_utils, "get_all_skills_dirs", lambda: [home / "skills"])
    store = memory_tool.MemoryStore()
    store.load_from_disk()
    return SimpleNamespace(
        skill=lambda **kw: skill_manager_tool.skill_manage(**kw),
        memory=lambda **kw: memory_tool.memory_tool(store=store, **kw),
    )


def _round(call_id, name, args, raw):
    return [
        {"role": "assistant", "tool_calls": [{
            "id": call_id, "function": {"name": name, "arguments": json.dumps(args)},
        }]},
        {"role": "tool", "tool_call_id": call_id, "content": raw},
    ]


def _skill_round(tools, call_id, **args):
    return _round(call_id, "skill_manage", args, tools.skill(**args))


def _memory_round(tools, call_id, **args):
    return _round(call_id, "memory", args, tools.memory(**args))


def _classify(messages, snapshot=()):
    return _classify_review_result(messages, list(snapshot))


def test_successful_skill_patches_do_not_pause_corrections(tools):
    create = _skill_round(tools, "c", action="create", name=SKILL, content=SKILL_BODY)
    assert json.loads(create[1]["content"])["success"]
    parent = SimpleNamespace(_learning_empty_streak=0)
    for i, (old, new) in enumerate([("zero", "one"), ("one", "two")]):
        messages = _skill_round(
            tools, f"p{i}", action="patch", name=SKILL, old_string=old, new_string=new
        )
        assert json.loads(messages[1]["content"])["success"]
        result = _classify(messages)
        assert result == "skill"
        note_review_result(parent, SIGNAL_CORRECTION, result)
    assert parent._learning_empty_streak == 0
    assert not review_paused(parent, SIGNAL_CORRECTION)


def test_real_patch_summary_is_not_what_decides_the_result(tools):
    _skill_round(tools, "c", action="create", name=SKILL, content=SKILL_BODY)
    messages = _skill_round(
        tools, "p", action="patch", name=SKILL, old_string="zero", new_string="one"
    )
    actions = summarize_background_review_actions(messages, [], notification_mode="on")
    assert actions and actions[0].startswith("Patched")
    assert _classify(messages) == "skill"


def test_genuinely_empty_reviews_still_pause(tools):
    skill_read = _round(
        "r", "skill_view", {"name": SKILL}, json.dumps({"success": True, "content": "x"})
    )
    failed_skill = _skill_round(
        tools, "f1", action="patch", name="missing-skill", old_string="a", new_string="b"
    )
    failed_memory = _round(
        "f2", "memory", {"action": "remove", "target": "memory", "old_text": "nope"},
        tools.memory(action="remove", target="memory", old_text="nope"),
    )
    assert not json.loads(failed_skill[1]["content"]).get("success")
    assert not json.loads(failed_memory[1]["content"]).get("success")
    cases = [[], skill_read, failed_skill, failed_memory + failed_skill]
    parent = SimpleNamespace(_learning_empty_streak=0)
    for messages in cases[:2]:
        result = _classify(messages)
        assert result == "none"
        note_review_result(parent, SIGNAL_CORRECTION, result)
    assert review_paused(parent, SIGNAL_CORRECTION)
    for messages in cases[2:]:
        assert _classify(messages) == "none"


def test_create_memory_and_combined_writes(tools):
    created = _skill_round(tools, "c", action="create", name=SKILL, content=SKILL_BODY)
    assert _classify(created) == "skill"
    added = _memory_round(tools, "m", action="add", target="memory", content="Prefers rubles")
    assert _classify(added) == "memory"
    profile = _memory_round(tools, "u", action="add", target="user", content="Writes tersely")
    assert _classify(profile) == "memory"
    assert _classify(created + added) == "skill+memory"


def test_inherited_and_unmatched_results_do_not_count(tools):
    messages = _memory_round(tools, "m", action="add", target="memory", content="Prefers rubles")
    assert _classify(messages, snapshot=[messages[1]]) == "none"
    orphan = [{"role": "tool", "tool_call_id": "zz", "content": '{"success": true}'}]
    assert _classify(orphan) == "none"
    assert _classify([messages[1]]) == "none"
