"""Planning must see the same ownership boundary that rejects review writes."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from agent import background_review, curator
from tools import skill_manager_tool as manager, skill_usage
from tools.skill_provenance import reset_current_write_origin, set_current_write_origin


@pytest.fixture
def skill_landscape(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    root = home / "skills"
    root.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text(yaml.safe_dump({
        "curator": {"prune_builtins": True},
        "skills": {"external_dirs": [str(external)]},
    }))

    paths = {}
    for name in ("pdf", "blocked-page-recovery", "plan", "user-owned", "user-null",
                 "agent-method", "adopted-method", "pinned-method", "hub-method",
                 "external-method"):
        path = (external if name == "external-method" else root) / name
        path.mkdir()
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A reusable method.\n---\n\n"
            "# Method\n\nOriginal procedure.\n"
        )
        paths[name] = path
    (root / ".bundled_manifest").write_text(
        "pdf:fake-hash\nblocked-page-recovery:fake-hash\nplan:fake-hash\n"
    )
    (root / ".hub").mkdir()
    (root / ".hub" / "lock.json").write_text(json.dumps({
        "installed": {"hub-method": {"install_path": "hub-method"}},
    }))
    for name in ("agent-method", "pinned-method", "hub-method", "external-method"):
        skill_usage.record_created(name, agent_created=True)
    skill_usage.record_created("user-null", agent_created=False)
    assert skill_usage.adopt_skill("adopted-method")[0]
    assert skill_usage.set_pinned("pinned-method", True)
    return home, paths


def _editable_names(prompt):
    line = next(line for line in prompt.splitlines()
                if line.startswith("Editable existing skills: "))
    names = line.removeprefix("Editable existing skills: ").removesuffix(".")
    return set() if names == "none" else set(names.split(", "))


@pytest.fixture
def review_request(monkeypatch):
    """Run the real worker; replace only the model-capable fork boundary."""
    request = Mock()
    fork = SimpleNamespace(
        _memory_enabled=True, _user_profile_enabled=True,
        _session_messages=[], run_conversation=request, release_clients=Mock(),
    )
    monkeypatch.setattr(background_review, "build_cache_parity_fork",
                        lambda *args, **kwargs: (fork, {}, False))
    return request


@pytest.mark.parametrize("review_memory,trigger,focus,attended", [
    (False, "counter", None, False),
    (True, "counter", None, False),
    (False, "correction", None, False),
    (True, "remember", None, False),
    (True, None, "Improve the PDF workflow", True),
])
def test_review_knows_editable_skills_before_planning(
    skill_landscape, review_request, review_memory, trigger, focus, attended,
):
    _, paths = skill_landscape
    agent = SimpleNamespace(_cached_system_prompt="stable-prefix")
    history = [{"role": "assistant", "content": "Here is the result."},
               {"role": "user", "content": "Remember: check the output first."}]
    original_history = deepcopy(history)
    target, prompt = background_review.spawn_background_review_thread(
        agent, history, review_memory=review_memory, review_skills=True,
        trigger=trigger, focus=focus, attended=attended, task_cfg={},
    )
    target()
    review_request.assert_called_once()
    delivered = review_request.call_args.kwargs["user_message"]
    assert delivered.startswith(prompt)
    assert "Editable existing skills:" not in prompt
    if trigger == "counter":
        expected = (background_review._COMBINED_REVIEW_PROMPT if review_memory
                    else background_review._SKILL_REVIEW_PROMPT)
        assert prompt == expected
        assert review_request.call_args.kwargs["conversation_history"] == history
    assert history == original_history
    editable = _editable_names(delivered)
    assert editable == {"agent-method", "adopted-method"}
    assert "never create a shadow copy or replacement" in delivered
    assert "All other existing skills are read-only" in delivered
    assert agent._cached_system_prompt == "stable-prefix"

    token = set_current_write_origin("background_review")
    try:
        # Real ownership enforcement agrees with the pre-plan inventory,
        # including unmanaged skills with missing and explicit-null records.
        for name, path in paths.items():
            blocked = manager._background_review_write_guard(name, path, "patch")
            assert (blocked is None) == (name in editable), name
    finally:
        reset_current_write_origin(token)


@pytest.mark.parametrize("dry_run", [False, True])
def test_curator_plans_only_writable_candidates(skill_landscape, monkeypatch, dry_run):
    delivered = []
    monkeypatch.setattr(curator, "_run_llm_review", lambda prompt: (
        delivered.append(prompt) or {"summary": "nothing to save", "tool_calls": []}
    ))
    curator.run_curator_review(synchronous=True, consolidate=True, dry_run=dry_run)
    assert len(delivered) == 1
    candidates = {line.split()[1] for line in delivered[0].splitlines()
                  if line.startswith("- ") and "provenance=" in line}
    assert candidates == {"agent-method", "adopted-method"}
    assert "PRUNE-BUILTINS MODE IS ON" not in delivered[0]
    assert "Do not create a shadow copy or replacement" in delivered[0]


@pytest.mark.parametrize("landscape", ["empty", "builtins", "pinned"])
def test_curator_skips_model_when_no_writable_candidates(
    skill_landscape, monkeypatch, landscape,
):
    _, paths = skill_landscape
    for name, path in paths.items():
        if landscape == "builtins" and name in {"pdf", "blocked-page-recovery", "plan"}:
            continue
        if landscape == "pinned" and name == "pinned-method":
            continue
        (path / "SKILL.md").unlink()
    calls = []
    monkeypatch.setattr(curator, "_run_llm_review", lambda prompt: calls.append(prompt))
    curator.run_curator_review(synchronous=True, consolidate=True, dry_run=True)
    assert not calls
    assert "skipped (no candidates)" in curator.load_state()["last_run_summary"]


def test_guard_still_blocks_mutations_but_foreground_can_edit(skill_landscape):
    _, paths = skill_landscape
    token = set_current_write_origin("background_review")
    try:
        for name in ("pdf", "blocked-page-recovery", "pinned-method", "user-owned"):
            result = json.loads(manager.skill_manage(
                action="patch", name=name, old_string="Original procedure.",
                new_string="Changed procedure.",
            ))
            assert not result["success"]
            assert "Refusing background curator" in result["error"]
            assert "Original procedure." in (paths[name] / "SKILL.md").read_text()
    finally:
        reset_current_write_origin(token)
    for name in ("pdf", "blocked-page-recovery", "pinned-method", "user-owned"):
        result = json.loads(manager.skill_manage(
            action="patch", name=name, old_string="Original procedure.",
            new_string="Changed procedure.",
        ))
        assert result["success"], result


@pytest.mark.parametrize("name", ["agent-method", "adopted-method"])
def test_editable_skills_still_learn_after_read(skill_landscape, name):
    from tools.skills_tool import skill_view

    _, paths = skill_landscape
    token = set_current_write_origin("background_review")
    try:
        manager._reset_background_review_read_marks()
        assert json.loads(skill_view(name))["success"]
        result = json.loads(manager.skill_manage(
            action="patch", name=name, old_string="Original procedure.",
            new_string="Verified reusable procedure.",
        ))
        assert result["success"], result
        assert "Verified reusable procedure." in (paths[name] / "SKILL.md").read_text()
    finally:
        manager._reset_background_review_read_marks()
        reset_current_write_origin(token)


def test_memory_only_review_does_not_scan_skills(skill_landscape, monkeypatch, review_request):
    def unexpected_scan():
        pytest.fail("Memory-only reviews must not scan the skill collection")
    monkeypatch.setattr(manager, "background_review_editable_skills", unexpected_scan, raising=False)
    target, prompt = background_review.spawn_background_review_thread(
        SimpleNamespace(), [], review_memory=True, task_cfg={},
    )
    target()
    review_request.assert_called_once()
    assert "Editable existing skills:" not in prompt
    assert "Editable existing skills:" not in review_request.call_args.kwargs["user_message"]


def test_review_planning_fails_closed_when_inventory_unavailable(monkeypatch, review_request):
    def unreadable():
        raise OSError("test unreadable inventory")
    monkeypatch.setattr(manager, "background_review_editable_skills", unreadable, raising=False)
    target, _prompt = background_review.spawn_background_review_thread(
        SimpleNamespace(), [], review_skills=True, task_cfg={},
    )
    target()
    review_request.assert_called_once()
    assert _editable_names(review_request.call_args.kwargs["user_message"]) == set()
