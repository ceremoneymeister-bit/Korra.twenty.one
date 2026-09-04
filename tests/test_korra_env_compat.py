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

import korra_constants
from korra_constants import (
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
    monkeypatch.setattr(korra_constants, "_default_hermes_root_memo", None)
    assert get_default_hermes_root() == root


# ── инвариант исходников ──────────────────────────────────────────────


# Проверяется рантайм движка. Вне периметра: комплекты скиллов и
# `scripts/` — самостоятельные файлы, которые запускаются своим
# интерпретатором и не обязаны импортировать korra_constants;
# фронтенды и десктоп — отдельный слой.
#
# Каталоги отсекаются ПО ПУТИ ОТ КОРНЯ, а не по имени: отсечение по имени
# заодно выкидывало `plugins/web/`, и пропущенная там запись пряталась от
# проверки.
_ENGINE_SKIP_ANY = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "hermes_agent.egg-info",
}
_ENGINE_SKIP_ROOTS = {
    "graphify-out", "tests", "web", "apps", "ui-tui", "native", "nix",
    "locales", "assets", "mcp-research-data", "docs", "skills",
    "optional-skills", "scripts", "optional-mcps", "evals",
    ".hermes-runtime",
}


def _engine_python_files():
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        rel_dir = Path(dirpath).relative_to(REPO_ROOT)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _ENGINE_SKIP_ANY
            and not (rel_dir == Path(".") and d in _ENGINE_SKIP_ROOTS)
        ]
        for filename in sorted(filenames):
            if not filename.endswith(".py") or filename == "setup.py":
                continue
            yield Path(dirpath) / filename


def test_engine_code_has_no_raw_env_writes():
    """Запись переменной пары мимо хелпера снова разъедет имена.

    Пропущенная запись `env["HERMES_HOME"] = ...` оставит дочернему процессу
    старое значение под вторым именем — ровно тот случай, когда профили
    начинают писать память друг другу. Инвариант держится тестом, а не
    внимательностью.
    """
    import re

    write_re = re.compile(r'\[\s*["\'](?:HERMES|KORRA)_[A-Z0-9_]+["\']\s*\]\s*=(?!=)')
    offenders = []
    for path in _engine_python_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            if write_re.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
        offenders.extend(_named_constant_env_writes(path, text))
    assert not offenders, (
        "Переменные пары KORRA_*/HERMES_* пишутся мимо korra_env_set/"
        "korra_env_expand:\n" + "\n".join(offenders)
    )


def _named_constant_env_writes(path, text):
    """Записи вида ``env[_SOME_ENV] = ...``, где константа держит имя пары.

    Литеральное имя видно грепом, а имя, спрятанное за модульной
    константой, — нет; именно так `gateway/media_policy.py` писал только
    одно имя из пары.
    """
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover — синтаксис ловит компиляция
        return []
    names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and node.value.value.startswith(("KORRA_", "HERMES_"))
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    if not names:
        return []
    found = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Name)
                and target.slice.id in names
            ):
                found.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno}: "
                    f"{ast.unparse(target)} = ..."
                )
    return found


def test_engine_code_has_no_raw_env_reads_by_variable():
    """Чтение ``os.environ.get(_SOME_ENV)`` мимо хелпера теряет второе имя.

    Литеральное имя ловится глазами и грепом, спрятанное за константой или
    переменной цикла — нет: так `korra_cli/web_server.py` перестал бы
    видеть `HERMES_DASHBOARD_FILES_ROOT`, который контур владельца задаёт
    снаружи, а `korra_cli/kanban.py` — `HERMES_PROFILE`.
    """
    import ast
    import re as _re

    pair_re = _re.compile(r"\b(?:KORRA|HERMES)_[A-Z0-9_]+\b")
    readers = {"get", "getenv", "pop", "setdefault"}
    offenders = []
    for path in _engine_python_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not pair_re.search(text):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover — синтаксис ловит компиляция
            continue
        # Имена, которым где-либо в файле присваивается или в которые
        # итерируется имя переменной окружения из нашей пары.
        names: set[str] = set()

        def _bind(target):
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for element in target.elts:
                    _bind(element)

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and pair_re.search(ast.unparse(node.value)):
                for target in node.targets:
                    _bind(target)
            elif isinstance(node, ast.For) and pair_re.search(ast.unparse(node.iter)):
                _bind(node.target)
        if not names:
            continue
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in readers
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in names
            ):
                continue
            base = ast.unparse(node.func.value)
            if base == "os" or base.endswith(("environ", "env")):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {ast.unparse(node)[:110]}"
                )
    assert not offenders, (
        "Переменные пары KORRA_*/HERMES_* читаются мимо korra_env:\n"
        + "\n".join(offenders)
    )
