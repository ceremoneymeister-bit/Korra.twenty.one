"""An image rejection strips the request, never the conversation.

A provider that answers an image-bearing request with a 4xx such as
"This model does not support images" says what the CURRENT model accepts,
not what the conversation holds. The recovery used to run
``_strip_images_from_messages`` on the canonical ``messages`` list: the
image parts were removed from the very dicts the caller passed in as
``conversation_history`` and returned as ``result["messages"]``, and
image-only messages were deleted from the list. The CLI and TUI keep that
list as the next turn's history, so after one text-only fallback or a
``/model`` switch the images a user had sent to a vision model were gone.

The strip now happens on the per-call request copy only. The images the
rejecting (provider, model) refused are remembered, so later requests to the
SAME model leave them out without another 4xx, any other model gets them
again, and a NEW image still reaches the same model — several rejection
wordings (ChatGPT-account Codex "does not represent a valid image", "failed
to decode image") are about one corrupt picture, not the model. Hermes
upstream 1ef306f68d remembers the whole model instead; ported to Korra's
inline recovery branch with that narrower memory.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_IMG_A = "data:image/png;base64,QUFBQQ=="
_IMG_B = "data:image/png;base64,QkJCQg=="
_IMG_C = "data:image/png;base64,Q0NDQw=="


class _ImageRejected(Exception):
    """Stand-in for an openai.BadRequestError from a text-only endpoint."""

    status_code = 400

    def __init__(self):
        super().__init__("This model does not support images.")
        self.body = {"error": {"message": "This model does not support images."}}
        self.response = None


def _ok(content: str):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="stop")],
        model="text-model",
        usage=None,
    )


def _image_urls(messages) -> list[str]:
    urls = []
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                urls.append(part["image_url"]["url"])
    return urls


@pytest.fixture()
def db_agent(tmp_path: Path):
    from korra_state import SessionDB
    from run_agent import AIAgent

    db = SessionDB(db_path=tmp_path / "state.db")
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI", return_value=MagicMock()),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            provider="openrouter",
            model="text/model",
            quiet_mode=True,
            session_db=db,
            session_id="20260923_120000_imgrej",
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    agent._api_max_retries = 3
    agent.compression_enabled = False
    agent.save_trajectories = False
    return agent, db


def _run(agent, user_message, history, replies):
    """Run one turn; ``replies`` is consumed per provider call (exception or text)."""
    calls: list[list] = []
    queue = list(replies)

    def fake_api_call(api_kwargs):
        # Snapshot: recovery mutates the request copy in place.
        calls.append(copy.deepcopy(api_kwargs.get("messages")))
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _ok(reply)

    with (
        patch.object(agent, "_interruptible_api_call", side_effect=fake_api_call),
        # The image reaches the provider as a native part only when the
        # model is considered vision-capable — that is the premise here.
        patch.object(agent, "_model_supports_vision", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch("run_agent.OpenAI", return_value=MagicMock()),
        patch("agent.agent_runtime_helpers.time.sleep"),
        patch("agent.model_metadata.get_model_context_length", return_value=200000),
        # Titling runs on a side thread through the auxiliary client; keep the
        # test offline.
        patch("agent.title_generator.maybe_auto_title"),
    ):
        result = agent.run_conversation(user_message, conversation_history=history)
    return result, calls


def _without_persist_marker(messages):
    """History as the caller sees it; the flush stamps its own bookkeeping key."""
    return [
        {k: v for k, v in m.items() if k != "_db_persisted"} for m in messages
    ]


def _prior_turn():
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is on this photo?"},
                {"type": "image_url", "image_url": {"url": _IMG_A}},
            ],
        },
        {"role": "assistant", "content": "A grey cat on a sofa."},
    ]


def _current_turn():
    return [
        {"type": "text", "text": "And on this one?"},
        {"type": "image_url", "image_url": {"url": _IMG_B}},
    ]


class TestImageRejectionKeepsConversation:
    def test_rejection_retries_text_only_but_keeps_every_image(self, db_agent):
        agent, db = db_agent
        history = _prior_turn()
        history_before = copy.deepcopy(history)

        result, calls = _run(
            agent, _current_turn(), history, [_ImageRejected(), "Text-only answer."]
        )

        assert result["completed"] is True
        assert result["final_response"] == "Text-only answer."
        # The rejected request carried both images; the retry carried none.
        assert len(calls) == 2
        assert _image_urls(calls[0]) == [_IMG_A, _IMG_B]
        assert _image_urls(calls[1]) == []

        # The caller's history dicts were not rewritten.
        assert _without_persist_marker(history) == history_before, (
            "the recovery rewrote the caller's history"
        )
        # The conversation handed back for the next turn still has both images.
        assert _image_urls(result["messages"]) == [_IMG_A, _IMG_B], (
            "images were stripped from the canonical conversation"
        )
        # The durable row of this turn keeps its image marker.
        user_rows = [r for r in db.get_messages(agent.session_id) if r["role"] == "user"]
        assert user_rows and user_rows[-1]["content"].endswith("[screenshot]")

    def test_image_only_message_survives_in_the_conversation(self, db_agent):
        agent, _db = db_agent
        history = [
            {"role": "user", "content": "Here is the chart."},
            {"role": "assistant", "content": "Send it over."},
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": _IMG_A}}],
            },
            {"role": "assistant", "content": "Got it."},
        ]

        result, calls = _run(
            agent, "Summarise it.", history, [_ImageRejected(), "Summary."]
        )

        assert result["completed"] is True
        assert _image_urls(calls[1]) == []
        assert _image_urls(result["messages"]) == [_IMG_A], (
            "the image-only message was deleted from the conversation"
        )
        assert len(history) == 4


class TestLaterTurnsFollowTheModel:
    def test_same_model_skips_refused_images_and_another_model_sees_them(self, db_agent):
        agent, _db = db_agent
        first, _ = _run(
            agent, _current_turn(), _prior_turn(), [_ImageRejected(), "Text-only answer."]
        )
        assert first["completed"] is True

        # Next turn on the same model: the refused images are not re-sent,
        # so there is no repeated 4xx round trip.
        second, calls = _run(agent, "Anything else?", first["messages"], ["No."])
        assert second["completed"] is True
        assert len(calls) == 1
        assert _image_urls(calls[0]) == []
        assert _image_urls(second["messages"]) == [_IMG_A, _IMG_B]

        # Switching to a model that accepts images sends them again.
        agent.model = "vision/model"
        third, calls = _run(agent, "Look again.", second["messages"], ["Two cats."])
        assert third["completed"] is True
        assert _image_urls(calls[0]) == [_IMG_A, _IMG_B], (
            "a vision model must see the images the conversation still holds"
        )

    def test_a_new_image_still_reaches_the_model_that_refused_an_old_one(self, db_agent):
        """One corrupt photo must not hide every later photo from that model."""
        agent, _db = db_agent
        first, _ = _run(
            agent, _current_turn(), _prior_turn(), [_ImageRejected(), "Could not read it."]
        )
        assert first["completed"] is True

        new_photo = [
            {"type": "text", "text": "Try this one instead."},
            {"type": "image_url", "image_url": {"url": _IMG_C}},
        ]
        second, calls = _run(agent, new_photo, first["messages"], ["A dog."])

        assert second["completed"] is True
        assert len(calls) == 1
        assert _image_urls(calls[0]) == [_IMG_C]
        assert _image_urls(second["messages"]) == [_IMG_A, _IMG_B, _IMG_C]

    def test_a_new_image_refused_again_is_recovered_in_its_turn(self, db_agent):
        agent, _db = db_agent
        first, _ = _run(
            agent, _current_turn(), _prior_turn(), [_ImageRejected(), "Text-only answer."]
        )
        new_photo = [
            {"type": "text", "text": "And this?"},
            {"type": "image_url", "image_url": {"url": _IMG_C}},
        ]
        second, calls = _run(
            agent, new_photo, first["messages"], [_ImageRejected(), "Still text only."]
        )

        assert second["completed"] is True
        assert [_image_urls(c) for c in calls] == [[_IMG_C], []]
        assert _image_urls(second["messages"]) == [_IMG_A, _IMG_B, _IMG_C]


class TestMaxIterationsSummaryFollowsTheModel:
    """The iteration-limit summary builds its own request from ``messages``.

    It used to inherit the stripped conversation; now it must apply the same
    per-model strip itself, on its shallow copies, without touching history.
    """

    @staticmethod
    def _summary_request(agent, messages):
        from agent.chat_completion_helpers import handle_max_iterations

        captured = {}

        class _Completions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return "RAW"

        client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
        transport = SimpleNamespace(
            normalize_response=lambda _r: SimpleNamespace(content="SUMMARY")
        )
        with (
            patch.object(agent, "_ensure_primary_openai_client", return_value=client),
            patch.object(agent, "_get_transport", return_value=transport),
        ):
            assert handle_max_iterations(agent, messages, 5) == "SUMMARY"
        return captured["messages"]

    def test_rejecting_model_summary_leaves_out_refused_images(self, db_agent):
        from agent.message_sanitization import remember_rejected_images

        agent, _db = db_agent
        remember_rejected_images(agent, _prior_turn())
        messages = _prior_turn()

        sent = self._summary_request(agent, messages)

        assert _image_urls(sent) == []
        assert _image_urls(messages) == [_IMG_A], "the summary path rewrote history"

    def test_other_model_summary_keeps_images(self, db_agent):
        from agent.message_sanitization import remember_rejected_images

        agent, _db = db_agent
        remember_rejected_images(agent, _prior_turn())
        agent.model = "vision/model"

        sent = self._summary_request(agent, _prior_turn())

        assert _image_urls(sent) == [_IMG_A]


class TestRejectedImageMemory:
    @staticmethod
    def _agent(provider="p", model="m"):
        return SimpleNamespace(provider=provider, model=model)

    def test_strips_refused_images_only_for_the_rejecting_model(self):
        from agent.message_sanitization import (
            remember_rejected_images,
            strip_images_for_rejecting_model,
        )

        agent = self._agent()
        remember_rejected_images(agent, _prior_turn())

        msgs = _prior_turn()
        assert strip_images_for_rejecting_model(agent, msgs) is True
        assert _image_urls(msgs) == []
        assert msgs[0]["content"] == [{"type": "text", "text": "What is on this photo?"}]

        agent.model = "vision"
        msgs = _prior_turn()
        assert strip_images_for_rejecting_model(agent, msgs) is False
        assert _image_urls(msgs) == [_IMG_A]

    def test_unrecorded_image_is_kept_for_the_rejecting_model(self):
        from agent.message_sanitization import (
            remember_rejected_images,
            strip_images_for_rejecting_model,
        )

        agent = self._agent()
        remember_rejected_images(agent, _prior_turn())
        msgs = _prior_turn() + [
            {"role": "user", "content": [
                {"type": "text", "text": "new"},
                {"type": "image_url", "image_url": {"url": _IMG_C}},
            ]},
        ]

        assert strip_images_for_rejecting_model(agent, msgs) is True
        assert _image_urls(msgs) == [_IMG_C]

    def test_no_rejection_recorded_is_a_noop(self):
        from agent.message_sanitization import strip_images_for_rejecting_model

        msgs = _prior_turn()
        assert strip_images_for_rejecting_model(self._agent(), msgs) is False
        assert _image_urls(msgs) == [_IMG_A]

    def test_new_agent_starts_with_no_rejections(self, db_agent):
        agent, _db = db_agent
        assert agent._image_rejections == {}
