"""`korra` — каноническое имя команды форка, `hermes` — рабочий алиас (W2).

Владелец видит трассу инструментов, а скилл `korra-agent` объявляет
`korra --help` источником правды о сборке. Значит имя обязано существовать как
точка входа, а справка — называть его, иначе агент печатает команду, которой у
него в PATH нет.
"""

from __future__ import annotations

import pathlib
import tomllib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def console_scripts() -> dict:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["scripts"]


class TestConsoleScripts:
    def test_korra_entry_point_exists(self, console_scripts):
        assert "korra" in console_scripts

    def test_korra_and_hermes_share_one_entry_point(self, console_scripts):
        """Две точки входа, один main — иначе они разъедутся при первой правке."""
        assert console_scripts["korra"] == console_scripts["hermes"]

    def test_legacy_hermes_entry_point_is_not_dropped(self, console_scripts):
        """На `hermes` завязаны установленные обёртки, юниты и docker-шим."""
        assert console_scripts["hermes"] == "hermes_cli.main:main"


class TestProgName:
    @pytest.mark.parametrize(
        "argv0,expected",
        [
            ("/usr/local/bin/korra", "korra"),
            ("/opt/hermes/bin/korra", "korra"),
            # Легаси-алиас показывает себя: иначе пользователь скопирует из
            # примеров команду, которой у него может не быть.
            ("/opt/hermes/.venv/bin/hermes", "hermes"),
            # Всё остальное схлопывается в канон, чтобы в usage не полез путь
            # интерпретатора или pytest.
            ("/usr/bin/pytest", "korra"),
            ("python3", "korra"),
            ("", "korra"),
        ],
    )
    def test_resolve_prog_name(self, argv0, expected):
        from hermes_cli._parser import resolve_prog_name

        assert resolve_prog_name(argv0) == expected

    def test_usage_line_names_korra(self, monkeypatch):
        import sys

        from hermes_cli import _parser

        monkeypatch.setattr(sys, "argv", ["korra", "--help"])
        parser, _, _ = _parser.build_top_level_parser()

        assert parser.format_usage().startswith("usage: korra")

    def test_usage_line_honours_the_legacy_alias(self, monkeypatch):
        import sys

        from hermes_cli import _parser

        monkeypatch.setattr(sys, "argv", ["/opt/hermes/.venv/bin/hermes", "--help"])
        parser, _, _ = _parser.build_top_level_parser()

        assert parser.format_usage().startswith("usage: hermes")

    def test_examples_block_teaches_korra(self):
        from hermes_cli._parser import _EPILOGUE

        assert "korra --help" in _EPILOGUE or "korra <command> --help" in _EPILOGUE
        # Ни одной апстримовой команды в примерах: агент копирует их дословно.
        assert "hermes " not in _EPILOGUE

    def test_examples_reference_only_skills_that_exist(self):
        """`-s <skill>` в примере обязан называть реально поставляемый скилл.

        До форка тут стоял `hermes-agent-dev`, которого в дереве нет — пример
        учил агента команде, гарантированно падающей с «skill not found».
        """
        import re

        from hermes_cli._parser import _EPILOGUE

        referenced = set()
        for match in re.finditer(r"-s\s+([\w,-]+)", _EPILOGUE):
            referenced.update(match.group(1).split(","))

        assert referenced, "пример с -s пропал из справки"
        on_disk = {p.parent.name for p in (REPO / "skills").rglob("SKILL.md")}
        assert referenced <= on_disk, f"нет таких скиллов: {sorted(referenced - on_disk)}"

    def test_legacy_invocation_rewrites_examples_but_not_skill_names(self):
        """Под `hermes` примеры перепишутся, а имя скилла `korra-agent` — нет."""
        import re

        from hermes_cli._parser import CANONICAL_PROG, _EPILOGUE

        rewritten = re.sub(rf"\b{CANONICAL_PROG}(?![-\w])", "hermes", _EPILOGUE)

        assert "hermes chat -q" in rewritten
        assert "korra-agent" in rewritten, (
            "подмена имени команды не должна задевать имя скилла — иначе "
            "пример зовёт несуществующий hermes-agent"
        )
