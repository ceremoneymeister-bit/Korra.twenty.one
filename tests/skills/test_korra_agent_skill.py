"""The `korra-agent` skill is what a running Korra knows about itself.

`website/` is never packaged, so an installed Korra has no local copy of the
user guide; skills ARE synced into `$HERMES_HOME/skills/`. The skill therefore
has to carry its own routing: the index has to be where the skill says it is,
and every reference has to be reachable, otherwise a shipped feature is
invisible and the agent answers "Korra can't do that."

Korra: скилл переименован из `hermes-agent` в `korra-agent` (W2). Тесты ниже
дополнительно держат бренд: имя скилла, отсутствие апстримовой команды в
примерах и правдивость путей к каталогу данных.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "skills" / "autonomous-ai-agents" / "korra-agent"
SKILL_MD = SKILL_DIR / "SKILL.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def test_skill_directory_and_frontmatter_name_agree(skill_text):
    """`skill_view` резолвит и по каталогу, и по `name:` — они обязаны совпадать.

    Расхождение даёт худший вид отказа: `skills_list` показывает одно имя,
    `skill_view` по нему находит другой каталог (или не находит ничего).
    """
    match = re.search(r"^name:\s*(\S+)\s*$", skill_text, flags=re.MULTILINE)
    assert match, "во фронтматтере скилла нет поля name"
    assert match.group(1) == "korra-agent"
    assert SKILL_DIR.name == "korra-agent"


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


# ── Бренд: чего в скилле и его references быть не должно ─────────────────────
#
# Владелец видит трассу инструментов. Всё, что скилл предлагает выполнить,
# он выполнит буквально, поэтому апстримовое имя команды здесь — это не
# косметика, а то, что реально всплывёт в трассе.

SKILL_FILES = sorted(SKILL_DIR.rglob("*.md")) + sorted(SKILL_DIR.glob("templates/*"))

# `hermes` в позиции команды: начало строки/после ``` или `$`, дальше подкоманда
# или флаг. Env-переменные (HERMES_HOME), модули (korra_cli) и путь установки
# (/opt/hermes) под шаблон не попадают — они остаются намеренно.
_HERMES_COMMAND = re.compile(r"(?:^|[`$(\s])hermes\s+(?:-|[a-z][a-z-]*\s)", re.MULTILINE)


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.name)
def test_skill_files_do_not_teach_the_upstream_command(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = [m.group(0).strip() for m in _HERMES_COMMAND.finditer(text)]
    # `hermes-acp` — отдельная точка входа, её имя не менялось.
    hits = [h for h in hits if not h.startswith("hermes-acp")]
    assert not hits, (
        f"{path.relative_to(SKILL_DIR)} учит агента команде апстрима: {hits}. "
        "Каноническое имя команды форка — `korra`."
    )


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.name)
def test_skill_files_do_not_invent_a_korra_home(path: Path):
    """`~/.korra` в коде не существует — такой путь был бы ложью.

    Дефолт форка — `~/.hermes` (`korra_constants._get_platform_default_hermes_home`),
    в Docker-образе `HERMES_HOME=/opt/data`. Правильная форма в документации —
    `$HERMES_HOME`, и этот тест не даёт «обезбрендить» путь выдумкой.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    assert "~/.korra" not in text, (
        f"{path.relative_to(SKILL_DIR)} обещает каталог ~/.korra, которого нет в коде"
    )
