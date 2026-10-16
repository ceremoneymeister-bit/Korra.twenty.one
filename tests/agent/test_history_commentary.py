"""Display projection leaves byte-stable provider history untouched."""
import copy
import json

import pytest

from agent.history_commentary import project_history_commentary


def item(phase, text):
    return {"type": "message", "role": "assistant", "phase": phase,
            "content": [{"type": "output_text", "text": text}]}


@pytest.mark.parametrize("serialized", [False, True])
def test_projection_is_public_redacted_and_does_not_mutate_replay(tmp_path, serialized):
    (tmp_path / "config.yaml").write_text("display:\n  show_commentary: true\n")
    items = [item("analysis", "PRIVATE_ANALYSIS"),
             item("commentary", "Проверяю <think>PRIVATE_THINK</think> export OPENAI_API_KEY=sk-proj-fixtureOnly000000000000000000000"),
             item("final_answer", "Готово")]
    message = {"role": "assistant", "content": "Готово", "reasoning_content": "PRIVATE_ANALYSIS",
               "codex_message_items": json.dumps(items) if serialized else items}
    original = copy.deepcopy(message)
    result = project_history_commentary([message], home=tmp_path)[0]
    assert result["display_commentary"][0].startswith("Проверяю")
    assert "sk-proj-fixtureOnly000000000000000000000" not in result["display_commentary"][0]
    assert "PRIVATE" not in result["display_commentary"][0]
    assert result["display_reasoning"] == ""
    assert result["content"] == "Готово"
    assert message == original


def test_disabled_profile_and_analysis_only_do_not_leak(tmp_path):
    (tmp_path / "config.yaml").write_text("display:\n  show_commentary: false\n")
    rows = [{"role": "assistant", "content": "", "codex_message_items": [item(phase, "PRIVATE")],
             "reasoning_content": "PRIVATE"} for phase in ("analysis", "commentary")]
    result = project_history_commentary(rows, home=tmp_path)
    assert all(not row.get("display_commentary") and row["display_reasoning"] == "" for row in result)
