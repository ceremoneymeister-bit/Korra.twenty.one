"""K21-330: the board is only for what the owner explicitly asks for."""

from agent.prompt_builder import KANBAN_CHAT_GUIDANCE, KANBAN_GUIDANCE


def test_chat_guidance_requires_an_explicit_request():
    g = KANBAN_CHAT_GUIDANCE
    assert "only for what the owner explicitly asks for" in g
    assert "Do every other request yourself in this chat" in g
    assert "offer once, in one sentence" in g
    assert "create nothing until the owner agrees" in g


def test_chat_guidance_has_no_model_judged_trigger():
    g = KANBAN_CHAT_GUIDANCE
    assert "several steps" not in g
    assert "Create every step" not in g
    assert "Plan in one pass" not in g


def test_status_question_only_reads():
    g = KANBAN_CHAT_GUIDANCE
    assert "A question about status" in g
    assert "never creates, starts or restarts a card" in g


def test_create_tool_description_does_not_invite_the_chat_to_use_the_board():
    from tools.kanban_tools import KANBAN_CREATE_SCHEMA
    d = KANBAN_CREATE_SCHEMA["description"]
    assert "explicitly asked" in d
    assert "otherwise do the work yourself" in d


def test_worker_guidance_is_unchanged_in_scope():
    assert "Do not assign follow-up work to yourself" in KANBAN_GUIDANCE
