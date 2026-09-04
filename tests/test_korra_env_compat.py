"""Слой совместимости имён переменных окружения: KORRA_* поверх HERMES_*.

Контракт форка Korra 21: код читает новое имя, старое остаётся рабочим
навсегда, а в окружение дочернего процесса пишутся оба имени — контуры и
комплекты развёртывания задают старые имена снаружи, и внешние скрипты
контура читают их же.

Отдельный блок закрывает резолв домашнего каталога профиля: ошибка там даёт
самый дорогой класс багов — профили пишут память друг другу.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import hermes_constants
from hermes_constants import (
    get_default_hermes_root,
    get_hermes_home,
    get_process_hermes_home,
    korra_env,
    korra_env_aliases,
    korra_env_expand,
    korra_env_pop,
    korra_env_present,
    korra_env_set,
    korra_env_setdefault,
    reset_hermes_home_override,
    set_hermes_home_override,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── имена пары ────────────────────────────────────────────────────────


def test_aliases_pair_both_directions():
    assert korra_env_aliases("HERMES_HOME") == ("KORRA_HOME", "HERMES_HOME")
    assert korra_env_aliases("KORRA_HOME") == ("KORRA_HOME", "HERMES_HOME")


def test_aliases_pass_through_foreign_names():
    """Имя вне пары остаётся собой — хелпер безопасен для любого вызова."""
    assert korra_env_aliases("PATH") == ("PATH",)
    assert korra_env_aliases("TELEGRAM_BOT_TOKEN") == ("TELEGRAM_BOT_TOKEN",)
    # Ведущее подчёркивание в пару не входит: `_HERMES_GATEWAY` — внутренний
    # маркер процесса, а не переменная внешнего контракта.
    assert korra_env_aliases("_HERMES_GATEWAY") == ("_HERMES_GATEWAY",)


# ── чтение ────────────────────────────────────────────────────────────


def test_new_name_wins(monkeypatch):
    monkeypatch.setenv("HERMES_TEST_KNOB", "старое")
    monkeypatch.setenv("KORRA_TEST_KNOB", "новое")
    assert korra_env("HERMES_TEST_KNOB") == "новое"
    assert korra_env("KORRA_TEST_KNOB") == "новое"


def test_legacy_name_still_read(monkeypatch):
    """Контур владельца задаёт только старое имя — оно обязано работать."""
    monkeypatch.delenv("KORRA_TEST_KNOB", raising=False)
    monkeypatch.setenv("HERMES_TEST_KNOB", "старое")
    assert korra_env("HERMES_TEST_KNOB") == "старое"
    assert korra_env("KORRA_TEST_KNOB") == "старое"


def test_empty_new_name_falls_back_to_legacy(monkeypatch):
    monkeypatch.setenv("KORRA_TEST_KNOB", "")
    monkeypatch.setenv("HERMES_TEST_KNOB", "старое")
    assert korra_env("HERMES_TEST_KNOB") == "старое"


def test_empty_legacy_name_is_not_unset(monkeypatch):
    """Заданная пустой переменная отличима от незаданной — как у os.environ.get."""
    monkeypatch.delenv("KORRA_TEST_KNOB", raising=False)
    monkeypatch.setenv("HERMES_TEST_KNOB", "")
    assert korra_env("HERMES_TEST_KNOB", "по умолчанию") == ""


def test_empty_new_name_alone_is_not_unset(monkeypatch):
    monkeypatch.delenv("HERMES_TEST_KNOB", raising=False)
    monkeypatch.setenv("KORRA_TEST_KNOB", "")
    assert korra_env("KORRA_TEST_KNOB", "по умолчанию") == ""


def test_default_when_neither_name_set(monkeypatch):
    monkeypatch.delenv("KORRA_TEST_KNOB", raising=False)
    monkeypatch.delenv("HERMES_TEST_KNOB", raising=False)
    assert korra_env("HERMES_TEST_KNOB") is None
    assert korra_env("HERMES_TEST_KNOB", "по умолчанию") == "по умолчанию"


def test_matches_os_environ_get_while_no_korra_names_set(monkeypatch):
    """Пока ни одна KORRA_* не задана, чтение побайтово совпадает с прежним."""
    for value in ("значение", ""):
        monkeypatch.delenv("KORRA_TEST_KNOB", raising=False)
        monkeypatch.setenv("HERMES_TEST_KNOB", value)
        assert korra_env("HERMES_TEST_KNOB", "d") == os.environ.get("HERMES_TEST_KNOB", "d")
    monkeypatch.delenv("HERMES_TEST_KNOB", raising=False)
    assert korra_env("HERMES_TEST_KNOB", "d") == os.environ.get("HERMES_TEST_KNOB", "d")


def test_read_from_explicit_mapping():
    env = {"HERMES_TEST_KNOB": "старое"}
    assert korra_env("KORRA_TEST_KNOB", env=env) == "старое"
    env["KORRA_TEST_KNOB"] = "новое"
    assert korra_env("HERMES_TEST_KNOB", env=env) == "новое"


def test_present_sees_either_name(monkeypatch):
    monkeypatch.delenv("HERMES_TEST_KNOB", raising=False)
    monkeypatch.delenv("KORRA_TEST_KNOB", raising=False)
    assert not korra_env_present("HERMES_TEST_KNOB")
    monkeypatch.setenv("HERMES_TEST_KNOB", "")
    assert korra_env_present("KORRA_TEST_KNOB")


# ── запись ────────────────────────────────────────────────────────────


def test_set_writes_both_names():
    env: dict[str, str] = {}
    korra_env_set(env, "KORRA_HOME", "/opt/data")
    assert env == {"KORRA_HOME": "/opt/data", "HERMES_HOME": "/opt/data"}


def test_set_from_legacy_name_writes_both():
    env: dict[str, str] = {}
    korra_env_set(env, "HERMES_HOME", "/opt/data")
    assert env == {"KORRA_HOME": "/opt/data", "HERMES_HOME": "/opt/data"}


def test_set_foreign_name_writes_one():
    env: dict[str, str] = {}
    korra_env_set(env, "PATH", "/usr/bin")
    assert env == {"PATH": "/usr/bin"}


def test_expand_covers_both_names():
    assert korra_env_expand({"HERMES_HOME": "/opt/data", "PATH": "/usr/bin"}) == {
        "KORRA_HOME": "/opt/data",
        "HERMES_HOME": "/opt/data",
        "PATH": "/usr/bin",
    }


def test_setdefault_writes_both_when_unset():
    env: dict[str, str] = {}
    assert korra_env_setdefault(env, "HERMES_QUIET", "1") == "1"
    assert env == {"KORRA_QUIET": "1", "HERMES_QUIET": "1"}


def test_setdefault_keeps_existing_value():
    env = {"HERMES_QUIET": "0"}
    assert korra_env_setdefault(env, "KORRA_QUIET", "1") == "0"
    assert env == {"HERMES_QUIET": "0"}


def test_pop_removes_both_names():
    env = {"KORRA_HOME": "/новый", "HERMES_HOME": "/старый", "PATH": "/usr/bin"}
    assert korra_env_pop(env, "HERMES_HOME") == "/новый"
    assert env == {"PATH": "/usr/bin"}


def test_pop_returns_default_when_absent():
    env: dict[str, str] = {}
    assert korra_env_pop(env, "HERMES_HOME", "запасное") == "запасное"


# ── дочерний процесс ──────────────────────────────────────────────────


def test_child_process_sees_both_names(tmp_path):
    """Внешний скрипт контура читает старое имя — оно обязано доехать."""
    env = {**os.environ, **korra_env_expand({"HERMES_TEST_CHILD": "значение"})}
    result = subprocess.run(
        [sys.executable, "-c", "import os,json;print(json.dumps("
         "[os.environ.get('KORRA_TEST_CHILD'), os.environ.get('HERMES_TEST_CHILD')]))"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == ["значение", "значение"]


def test_child_shell_sees_legacy_name(tmp_path):
    """`$HERMES_HOME` в shell-скрипте контура продолжает работать."""
    if os.name == "nt":  # pragma: no cover - контур только POSIX
        pytest.skip("shell-контракт проверяется на POSIX")
    env = {**os.environ, **korra_env_expand({"KORRA_HOME": str(tmp_path)})}
    result = subprocess.run(
        ["sh", "-c", 'printf "%s|%s" "$KORRA_HOME" "$HERMES_HOME"'],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == f"{tmp_path}|{tmp_path}"


# ── резолв дома профиля: главный риск слоя ────────────────────────────


def test_profile_home_resolves_under_legacy_name(tmp_path, monkeypatch):
    home = tmp_path / "profiles" / "secretary"
    home.mkdir(parents=True)
    monkeypatch.delenv("KORRA_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert get_hermes_home() == home
    assert get_process_hermes_home() == home


def test_profile_home_resolves_under_new_name(tmp_path, monkeypatch):
    home = tmp_path / "profiles" / "secretary"
    home.mkdir(parents=True)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("KORRA_HOME", str(home))
    assert get_hermes_home() == home
    assert get_process_hermes_home() == home


def test_new_name_wins_over_legacy_for_profile_home(tmp_path, monkeypatch):
    старый = tmp_path / "profiles" / "старый"
    новый = tmp_path / "profiles" / "новый"
    старый.mkdir(parents=True)
    новый.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(старый))
    monkeypatch.setenv("KORRA_HOME", str(новый))
    assert get_hermes_home() == новый


def test_context_override_still_beats_both_names(tmp_path, monkeypatch):
    """ContextVar-override остаётся сильнее любого имени переменной."""
    home = tmp_path / "profiles" / "worker"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "старый"))
    monkeypatch.setenv("KORRA_HOME", str(tmp_path / "новый"))
    token = set_hermes_home_override(home)
    try:
        assert get_hermes_home() == home
        # Процессный резолв override игнорирует — это его контракт.
        assert get_process_hermes_home() == tmp_path / "новый"
    finally:
        reset_hermes_home_override(token)
    assert get_hermes_home() == tmp_path / "новый"


@pytest.mark.parametrize("name", ["HERMES_HOME", "KORRA_HOME"])
def test_default_root_of_docker_profile_layout(tmp_path, monkeypatch, name):
    """`<root>/profiles/<имя>` даёт root под обоими именами (раскладка контура)."""
    root = tmp_path / "data"
    home = root / "profiles" / "secretary"
    home.mkdir(parents=True)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("KORRA_HOME", raising=False)
    monkeypatch.setenv(name, str(home))
    monkeypatch.setattr(hermes_constants, "_default_hermes_root_memo", None)
    assert get_default_hermes_root() == root


# ── инвариант исходников ──────────────────────────────────────────────


def test_engine_code_has_no_raw_env_writes():
    """Запись переменной пары мимо хелпера снова разъедет имена.

    Пропущенная запись `env["HERMES_HOME"] = ...` оставит дочернему процессу
    старое значение под вторым именем — ровно тот случай, когда профили
    начинают писать память друг другу. Инвариант держится тестом, а не
    внимательностью.
    """
    import re

    write_re = re.compile(r'\[\s*["\'](?:HERMES|KORRA)_[A-Z0-9_]+["\']\s*\]\s*=(?!=)')
    skip_dirs = {
        ".git", "node_modules", "__pycache__", "graphify-out", ".venv", "venv",
        "hermes_agent.egg-info", "tests", "web", "apps", "ui-tui", "native",
        "nix", "locales", "assets", "mcp-research-data", ".mypy_cache",
        ".pytest_cache", "docs", "skills", "optional-skills",
    }
    offenders = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for filename in filenames:
            if not filename.endswith(".py") or filename == "setup.py":
                continue
            path = Path(dirpath) / filename
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
            ):
                if write_re.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Переменные пары KORRA_*/HERMES_* пишутся мимо korra_env_set/"
        "korra_env_expand:\n" + "\n".join(offenders)
    )
