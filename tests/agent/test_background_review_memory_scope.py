"""Background review memory access follows the trigger that fired.

A skill-only nudge must not inherit the memory tool merely because the profile
has memory enabled. Unknown legacy callers also fail closed to a memoryless
review, while an explicit memory review retains the append-only surface.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import agent.background_review as background_review


def _review_agent(
    *,
    memory_enabled: bool = True,
    user_profile_enabled: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        _memory_enabled=memory_enabled,
        _user_profile_enabled=user_profile_enabled,
    )


class TestReviewToolWhitelistScope:
    def test_skill_only_review_omits_memory_tool(self):
        whitelist, _extra = background_review._review_tool_whitelist(
            _review_agent(),
            None,
            review_memory=False,
        )

        assert "memory" not in whitelist
        assert "skill_manage" in whitelist

    def test_memory_review_keeps_memory_tool(self):
        whitelist, _extra = background_review._review_tool_whitelist(
            _review_agent(),
            None,
            review_memory=True,
        )

        assert "memory" in whitelist

    def test_memory_disabled_profile_stays_memory_free(self):
        whitelist, _extra = background_review._review_tool_whitelist(
            _review_agent(memory_enabled=False),
            None,
            review_memory=True,
        )

        assert "memory" not in whitelist

    def test_default_scope_is_memoryless_fail_closed(self):
        whitelist, _extra = background_review._review_tool_whitelist(
            _review_agent(),
            None,
        )

        assert "memory" not in whitelist

    def test_extra_tools_cannot_bypass_memory_trigger(self):
        whitelist, extra = background_review._review_tool_whitelist(
            _review_agent(),
            {"extra_tools": ["memory", "proposal_tool"]},
            review_memory=False,
        )

        assert "memory" not in whitelist
        assert extra == {"proposal_tool"}
        assert "proposal_tool" in whitelist


class TestSpawnForwardsScope:
    def test_target_passes_review_memory_to_worker(self):
        captured = []

        def fake_worker(
            agent,
            messages_snapshot,
            prompt,
            task_cfg=None,
            review_run=None,
            review_memory=False,
        ):
            captured.append(review_memory)

        agent = SimpleNamespace()
        with patch.object(
            background_review,
            "_run_review_in_thread",
            fake_worker,
        ):
            skill_target, _prompt = background_review.spawn_background_review_thread(
                agent,
                [],
                review_memory=False,
                review_skills=True,
            )
            skill_target()

            memory_target, _prompt = background_review.spawn_background_review_thread(
                agent,
                [],
                review_memory=True,
                review_skills=False,
            )
            memory_target()

        assert captured == [False, True]
