"""Промах по имени скилла должен вести к нужному скиллу, а не в тупик (W2).

Два наблюдаемых отказа, ради которых это написано:

* Скилл `hermes-agent` переименован в `korra-agent`. Сохранённые сессии, память
  и клиентские конфиги держат старое имя; обратно — контур, обновлённый на новый
  образ без миграции каталога данных, держит на диске старое. Резолвер обязан
  находить скилл по любому из двух имён, но только если он реально есть.
* Модель обрезала имя до `-agent` и получала «skill not found» со списком из 20
  случайных скиллов — ни одной зацепки, чтобы исправиться со второй попытки.
"""

import json
from pathlib import Path

import pytest

from tools.skills_tool import _suggest_skill_names, skill_view


def _make_skill(root: Path, category: str, name: str) -> Path:
    d = root / "skills" / category / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Operating manual for tests.\n---\n"
        f"# {name}\n\nBody of {name}.\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".hermes"
    (h / "skills").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(h))
    return h


def _view(name: str) -> dict:
    return json.loads(skill_view(name))


class TestLegacyNameAlias:
    def test_legacy_name_resolves_to_renamed_skill(self, home):
        """`hermes-agent` из старой сессии должен открыть `korra-agent`."""
        _make_skill(home, "autonomous-ai-agents", "korra-agent")

        r = _view("hermes-agent")

        assert r["success"] is True, r
        assert "Body of korra-agent" in r["content"]

    def test_canonical_name_resolves_to_unmigrated_legacy_skill(self, home):
        """Обратная сторона: образ новый, каталог данных ещё старый.

        Системный промпт называет `korra-agent`, а на диске лежит `hermes-agent`.
        Без обратного алиаса агент терял бы указатель на собственную инструкцию.
        """
        _make_skill(home, "autonomous-ai-agents", "hermes-agent")

        r = _view("korra-agent")

        assert r["success"] is True, r
        assert "Body of hermes-agent" in r["content"]

    def test_alias_does_not_fabricate_a_missing_skill(self, home):
        """Алиас — не подмена: если синонима нет на диске, это честный отказ."""
        _make_skill(home, "media", "gif-search")

        r = _view("hermes-agent")

        assert r["success"] is False
        assert "not found" in r["error"]

    def test_alias_pair_does_not_recurse_forever(self, home):
        """Пара `hermes-agent` ⇄ `korra-agent` не должна зациклиться.

        Ни одного из двух имён на диске нет — резолвер обязан вернуть отказ,
        а не уйти в бесконечную рекурсию по алиасу.
        """
        _make_skill(home, "media", "gif-search")

        for probe in ("hermes-agent", "korra-agent"):
            r = _view(probe)
            assert r["success"] is False, probe

    def test_both_names_present_prefers_the_exact_match(self, home):
        """Пока имя резолвится штатно, алиас в дело не вступает."""
        _make_skill(home, "autonomous-ai-agents", "korra-agent")
        _make_skill(home, "legacy", "hermes-agent")

        assert "Body of korra-agent" in _view("korra-agent")["content"]
        assert "Body of hermes-agent" in _view("hermes-agent")["content"]


class TestTruncatedNameSuggestion:
    def test_truncated_name_suggests_the_full_one(self, home):
        """Ровно наблюдавшийся отказ: модель написала `-agent`."""
        _make_skill(home, "autonomous-ai-agents", "korra-agent")

        r = _view("-agent")

        assert r["success"] is False
        assert r["did_you_mean"] == ["korra-agent"]
        assert "korra-agent" in r["hint"]
        # Подсказка обязана назвать причину, иначе модель повторит обрезку.
        assert "exact" in r["hint"].lower()

    def test_typo_suggests_the_intended_skill(self, home):
        _make_skill(home, "autonomous-ai-agents", "korra-agent")

        r = _view("korra-agnet")

        assert r["success"] is False
        assert "korra-agent" in r["did_you_mean"]

    def test_no_suggestion_when_nothing_is_close(self, home):
        """Случайная подсказка хуже её отсутствия — молчим, если не похоже."""
        _make_skill(home, "autonomous-ai-agents", "korra-agent")

        r = _view("zzzzzzzz")

        assert r["success"] is False
        assert "did_you_mean" not in r
        assert r["available_skills"] == ["korra-agent"]


class TestSuggestionRanking:
    """`_suggest_skill_names` отдельно — ранжирование, а не только факт хита."""

    NAMES = ["korra-agent", "korra-skill-authoring", "github", "gif-search"]

    @pytest.mark.parametrize(
        "probe,expected_first",
        [
            ("-agent", "korra-agent"),
            ("agent", "korra-agent"),
            ("korra-agnet", "korra-agent"),
            ("KORRA-AGENT", "korra-agent"),
            ("githb", "github"),
        ],
    )
    def test_closest_name_ranks_first(self, probe, expected_first):
        assert _suggest_skill_names(probe, self.NAMES)[0] == expected_first

    def test_empty_probe_suggests_nothing(self):
        assert _suggest_skill_names("", self.NAMES) == []
        assert _suggest_skill_names("   ", self.NAMES) == []

    def test_result_is_capped(self):
        names = [f"skill-{i}" for i in range(50)]
        assert len(_suggest_skill_names("skill", names, limit=3)) == 3
