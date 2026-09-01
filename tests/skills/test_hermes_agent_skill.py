"""The `hermes-agent` skill is what a running Hermes knows about itself.

`website/` is never packaged, so an installed Hermes has no local copy of the
user guide; skills ARE synced into `$HERMES_HOME/skills/`. The skill therefore
does not try to restate the product — it routes to the published `llms.txt`,
which is generated from the docs tree on every build and so can never be behind
the feature set. These tests keep that routing honest: the index has to be where
the skill says it is, and every reference has to be reachable, otherwise a
shipped feature is invisible and the agent answers "Hermes can't do that."
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "skills" / "autonomous-ai-agents" / "hermes-agent"
SKILL_MD = SKILL_DIR / "SKILL.md"
GENERATOR = REPO / "website" / "scripts" / "generate-llms-txt.py"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def test_every_referenced_file_exists(skill_text):
    """Routing a question to a file that isn't there is a dead end."""
    targets = set(re.findall(r"`((?:references|templates)/[^`]+)`", skill_text))

    assert targets, "the skill's routing table no longer references any files"
    for target in sorted(targets):
        assert (SKILL_DIR / target).exists(), f"SKILL.md routes to missing {target}"


def test_every_reference_is_reachable_from_the_skill(skill_text):
    """An unrouted reference is one the agent will never think to open.

    This is the failure that produced the original complaint: content can exist
    and still be invisible because nothing points at it.
    """
    on_disk = {f"references/{path.name}" for path in (SKILL_DIR / "references").glob("*.md")}
    routed = set(re.findall(r"`(references/[^`]+)`", skill_text))

    assert not (on_disk - routed), (
        f"reference files no reader will ever reach: {sorted(on_disk - routed)} — "
        "add a routing-table row in SKILL.md"
    )


def test_skill_does_not_route_to_upstream_docs(skill_text):
    """Korra — жёсткий форк: документация Nous описывает другой код.

    Заменяет два прежних теста, которые ТРЕБОВАЛИ маршрутизации на
    hermes-agent.nousresearch.com и на генератор из удалённого website/.
    Инвариант перевёрнут: агент не должен отправлять пользователя туда.
    """
    assert "nousresearch.com" not in skill_text, (
        "скилл направляет агента на документацию апстрима — она описывает "
        "другой код и расходится с этой сборкой"
    )
    assert "--help" in skill_text, "не осталось указания на источник правды (CLI)"
