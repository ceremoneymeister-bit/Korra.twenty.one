"""Экран «Данные»: панель отдаёт ввод методолога валидатору движка.

Главное, что здесь защищается: панель НЕ решает сама, что данные верны, и не
подменяет текст отказа своим. Валидатор один — в `metal-calc-admin`; если
панель начнёт судить сама, два свода правил разойдутся в деньгах и молча.
"""

import asyncio
import json
from pathlib import Path

import pytest

from fastapi import HTTPException

from korra_cli.web_routers import calc_rates


PACK = {"schema_version": 2, "materials": {}, "pricing": {}}


@pytest.fixture
def rates(tmp_path, monkeypatch):
    root = tmp_path / "rates"
    root.mkdir()
    monkeypatch.setattr(calc_rates, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(
        calc_rates,
        "load_config",
        lambda: {
            "mcp_servers": {
                "metal_calc": {
                    "command": str(tmp_path / "venv" / "bin" / "metal-calc-mcp"),
                    "env": {"METAL_CALC_RATES_ROOT": str(root)},
                }
            }
        },
    )
    return root


def _fake_admin(tmp_path: Path, script: str) -> Path:
    """Подставной CLI: тесты проверяют тракт панели, а не движок."""
    binary = tmp_path / "venv" / "bin" / "metal-calc-admin"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/usr/bin/env python3\n" + script, encoding="utf-8")
    binary.chmod(0o755)
    return binary


# ── где панель ищет данные и бинарь ──────────────────────────────────────


def test_rates_root_follows_contour_config(rates, tmp_path):
    """Каталог берём оттуда же, откуда MCP.

    Своя константа означала бы, что человек правит файл, которого никто не
    читает, — и узнает об этом только по неверной цене.
    """
    assert calc_rates._rates_root() == rates


def test_rates_root_default_matches_the_engine(tmp_path, monkeypatch):
    """Умолчание обязано совпадать с умолчанием CLI, а не с домом агента.

    Разошедшись, они дают худший из отказов: публикация уходит в один
    каталог, панель читает другой и показывает «данные ещё не заведены»
    поверх заведённых — это не выглядит ошибкой, поэтому его ищут долго.
    """
    monkeypatch.setattr(calc_rates, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_rates, "load_config", lambda: {})
    assert calc_rates._rates_root() == Path("/etc/metal-calc/rates")


def test_admin_binary_sits_next_to_the_mcp_server(rates, tmp_path):
    binary = _fake_admin(tmp_path, "pass\n")
    assert calc_rates._admin_binary() == binary


def test_admin_binary_env_override_wins(rates, tmp_path, monkeypatch):
    _fake_admin(tmp_path, "pass\n")
    monkeypatch.setenv("METAL_CALC_ADMIN_BIN", "/custom/metal-calc-admin")
    assert calc_rates._admin_binary() == Path("/custom/metal-calc-admin")


def test_broken_config_does_not_break_the_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(calc_rates, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(
        calc_rates, "load_config", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert calc_rates._rates_root() == Path("/etc/metal-calc/rates")


# ── чтение ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_says_not_configured_instead_of_failing(rates):
    """Пустой контур открывает экран, а не показывает ошибку.

    «Данные ещё не заведены» — это ровно то состояние, ради которого человек
    сюда и пришёл.
    """
    payload = await calc_rates.calc_rates_active()
    assert payload == {"revision": None, "pack": None, "configured": False}


@pytest.mark.asyncio
async def test_active_returns_the_published_pack(rates):
    (rates / "r20260827-091500-md.json").write_text(json.dumps(PACK), encoding="utf-8")
    (rates / "_active.json").write_text(
        json.dumps({"revision": "r20260827-091500-md", "sha256": "abc"}), encoding="utf-8"
    )
    payload = await calc_rates.calc_rates_active()
    assert payload["configured"] is True
    assert payload["revision"] == "r20260827-091500-md"
    assert payload["pack"]["schema_version"] == 2


@pytest.mark.asyncio
async def test_active_reports_a_dangling_pointer(rates):
    (rates / "_active.json").write_text(json.dumps({"revision": "r-missing"}), encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        await calc_rates.calc_rates_active()
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_revision_preview_hides_service_files(rates):
    """Служебные файлы нельзя достать через маршрут ревизии.

    Их имена начинаются с подчёркивания именно затем, чтобы их нельзя было
    запросить как ревизию, — маршрут обязан держать ту же границу.
    """
    (rates / "_journal.jsonl").write_text("{}\n", encoding="utf-8")
    for name in ("_journal.jsonl", "_active", "../secrets"):
        with pytest.raises(HTTPException) as exc:
            await calc_rates.calc_rates_revision(name)
        assert exc.value.status_code == 404


# ── черновик ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_draft_round_trip(rates):
    empty = await calc_rates.calc_rates_draft_get()
    assert empty["exists"] is False
    assert empty["version"] is None
    saved = await calc_rates.calc_rates_draft_put(
        {"draft": {"materials": {"x": 1}}, "expected_version": None}
    )
    payload = await calc_rates.calc_rates_draft_get()
    assert payload["exists"] is True
    assert payload["draft"]["materials"]["x"] == 1
    assert payload["version"] == saved["version"]


@pytest.mark.asyncio
async def test_draft_is_not_a_revision(rates):
    """Черновик не должен попадать в историю ревизий и к агентам."""
    await calc_rates.calc_rates_draft_put(
        {"draft": {"materials": {}}, "expected_version": None}
    )
    assert (rates / "_draft.json").exists()
    with pytest.raises(HTTPException):
        await calc_rates.calc_rates_revision("_draft")


@pytest.mark.asyncio
async def test_oversized_body_is_refused(rates):
    with pytest.raises(HTTPException) as exc:
        await calc_rates.calc_rates_draft_put(
            {
                "draft": {"junk": "x" * (calc_rates.MAX_BODY_BYTES + 10)},
                "expected_version": None,
            }
        )
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_draft_compare_and_swap_prevents_stale_tab_overwrite(rates):
    first = await calc_rates.calc_rates_draft_put(
        {"draft": {"materials": {"first": 1}}, "expected_version": None}
    )
    second = await calc_rates.calc_rates_draft_put(
        {
            "draft": {"materials": {"second": 2}},
            "expected_version": first["version"],
        }
    )

    with pytest.raises(HTTPException) as stale:
        await calc_rates.calc_rates_draft_put(
            {
                "draft": {"materials": {"stale": 3}},
                "expected_version": first["version"],
            }
        )

    assert stale.value.status_code == 409
    current = await calc_rates.calc_rates_draft_get()
    assert current["version"] == second["version"]
    assert current["draft"] == {"materials": {"second": 2}}


@pytest.mark.asyncio
async def test_invalid_existing_draft_is_not_silently_overwritten(rates):
    (rates / calc_rates.DRAFT_NAME).write_text("{broken", encoding="utf-8")

    with pytest.raises(HTTPException) as blocked:
        await calc_rates.calc_rates_draft_put(
            {"draft": {"materials": {}}, "expected_version": None}
        )

    assert blocked.value.status_code == 503
    assert (rates / calc_rates.DRAFT_NAME).read_text(encoding="utf-8") == "{broken"


# ── тракт до CLI ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_passes_the_body_to_the_cli(rates, tmp_path):
    _fake_admin(
        tmp_path,
        "import json, sys\n"
        "body = json.load(sys.stdin)\n"
        "print(json.dumps({'ok': True, 'got': sorted(body)}))\n",
    )
    result = await calc_rates.calc_rates_validate({"b": 1, "a": 2})
    assert result == {"ok": True, "got": ["a", "b"]}


@pytest.mark.asyncio
async def test_validator_message_reaches_the_person_verbatim(rates, tmp_path):
    """Текст отказа — единственная подсказка, какую строку чинить.

    Подменять его на «ошибка сохранения» значит отправить человека искать
    вслепую среди четырёх десятков материалов.
    """
    _fake_admin(
        tmp_path,
        "import json, sys\n"
        "print(json.dumps({'error': {'code': 'InvalidRatePack',"
        " 'message': 'materials.09g2s: Rate source ref is required'}}))\n"
        "sys.exit(2)\n",
    )
    with pytest.raises(HTTPException) as exc:
        await calc_rates.calc_rates_validate({})
    assert exc.value.status_code == 400
    assert exc.value.detail == "materials.09g2s: Rate source ref is required"


@pytest.mark.asyncio
async def test_admin_error_code_can_be_mapped_for_stateful_commands(rates, tmp_path):
    _fake_admin(
        tmp_path,
        "import json, sys\n"
        "print(json.dumps({'error': {'code': 'RevisionConflict',"
        " 'message': 'expected revision 17, current revision 18'}}))\n"
        "sys.exit(2)\n",
    )
    with pytest.raises(HTTPException) as exc:
        await calc_rates._run_admin(
            ["qa-verdict"],
            error_statuses={"RevisionConflict": 409},
            default_error_status=422,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "expected revision 17, current revision 18"


@pytest.mark.asyncio
async def test_missing_cli_is_a_clear_message(rates):
    with pytest.raises(HTTPException) as exc:
        await calc_rates.calc_rates_validate({})
    assert exc.value.status_code == 503
    assert "не установлен" in exc.value.detail


@pytest.mark.asyncio
async def test_garbage_from_cli_is_not_shown_as_data(rates, tmp_path):
    _fake_admin(tmp_path, "print('not json at all')\n")
    with pytest.raises(HTTPException) as exc:
        await calc_rates.calc_rates_validate({})
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_publish_drops_the_draft(rates, tmp_path):
    """Опубликованный черновик перестаёт существовать.

    Иначе следующий заход предложит вернуться к копии, которая уже устарела.
    """
    _fake_admin(
        tmp_path,
        "import json, sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({'ok': True, 'revision': 'r1-md'}))\n",
    )
    saved = await calc_rates.calc_rates_draft_put(
        {"draft": {"materials": {}}, "expected_version": None}
    )
    result = await calc_rates.calc_rates_publish(
        {
            "pack": PACK,
            "note": "первые прайсы",
            "expected_draft_version": saved["version"],
        }
    )
    assert result["revision"] == "r1-md"
    assert not (rates / "_draft.json").exists()


@pytest.mark.asyncio
async def test_publish_rejection_keeps_the_draft(rates, tmp_path):
    """Invalid schema4 input must fail closed and remain recoverable."""
    _fake_admin(
        tmp_path,
        "import json, sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({'error': {'code': 'InvalidRatePack',"
        " 'message': 'rate_registry.svc:laser.grid: expected 3 rows'}}))\n"
        "sys.exit(2)\n",
    )
    draft = {"schema_version": 4, "rate_registry": {"svc:laser": {"grid": [[1]]}}}
    saved = await calc_rates.calc_rates_draft_put(
        {"draft": draft, "expected_version": None}
    )

    with pytest.raises(HTTPException) as exc:
        await calc_rates.calc_rates_publish(
            {
                "pack": draft,
                "note": "matrix edit",
                "expected_draft_version": saved["version"],
            }
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "rate_registry.svc:laser.grid: expected 3 rows"
    assert json.loads((rates / calc_rates.DRAFT_NAME).read_text(encoding="utf-8")) == draft


@pytest.mark.asyncio
async def test_publish_forwards_author_and_note(rates, tmp_path):
    _fake_admin(
        tmp_path,
        "import json, sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({'argv': sys.argv[1:]}))\n",
    )
    result = await calc_rates.calc_rates_publish(
        {
            "pack": PACK,
            "note": "подняли ставку токарки",
            "author": "tg758050420",
            "expected_draft_version": None,
        }
    )
    assert result["argv"] == [
        "pack-publish",
        "--author",
        "tg758050420",
        "--note",
        "подняли ставку токарки",
    ]


@pytest.mark.asyncio
async def test_publish_rejects_a_stale_draft_version_without_running_cli(
    rates, monkeypatch
):
    first = await calc_rates.calc_rates_draft_put(
        {"draft": {"materials": {"first": 1}}, "expected_version": None}
    )
    second = await calc_rates.calc_rates_draft_put(
        {
            "draft": {"materials": {"newer": 2}},
            "expected_version": first["version"],
        }
    )

    async def must_not_run(*args, **kwargs):
        raise AssertionError("stale publish reached the stateful CLI")

    monkeypatch.setattr(calc_rates, "_run_admin", must_not_run)
    with pytest.raises(HTTPException) as stale:
        await calc_rates.calc_rates_publish(
            {
                "pack": PACK,
                "note": "stale tab",
                "expected_draft_version": first["version"],
            }
        )

    assert stale.value.status_code == 409
    current = await calc_rates.calc_rates_draft_get()
    assert current["version"] == second["version"]
    assert current["draft"] == {"materials": {"newer": 2}}


@pytest.mark.asyncio
async def test_publish_serializes_a_concurrent_draft_write(rates, monkeypatch):
    saved = await calc_rates.calc_rates_draft_put(
        {"draft": {"materials": {"published": 1}}, "expected_version": None}
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_publish(*args, **kwargs):
        started.set()
        await release.wait()
        return {"ok": True, "revision": "r-published"}

    monkeypatch.setattr(calc_rates, "_run_admin", slow_publish)
    publishing = asyncio.create_task(
        calc_rates.calc_rates_publish(
            {
                "pack": PACK,
                "note": "publish",
                "expected_draft_version": saved["version"],
            }
        )
    )
    await started.wait()
    competing = asyncio.create_task(
        calc_rates.calc_rates_draft_put(
            {
                "draft": {"materials": {"competing": 2}},
                "expected_version": saved["version"],
            }
        )
    )
    await asyncio.sleep(0)
    assert not competing.done()
    release.set()
    assert (await publishing)["revision"] == "r-published"
    with pytest.raises(HTTPException) as stale:
        await competing
    assert stale.value.status_code == 409
    assert (await calc_rates.calc_rates_draft_get())["exists"] is False


@pytest.mark.asyncio
async def test_activate_requires_a_revision(rates, tmp_path):
    _fake_admin(tmp_path, "import json; print(json.dumps({'ok': True}))\n")
    with pytest.raises(HTTPException) as exc:
        await calc_rates.calc_rates_activate({})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_activate_archives_draft_so_it_cannot_mask_rollback(rates, tmp_path):
    _fake_admin(
        tmp_path,
        "import json, sys; print(json.dumps({'ok': True, 'revision': sys.argv[2]}))\n",
    )
    draft = {"materials": {"hours-of-work": 1}}
    saved = await calc_rates.calc_rates_draft_put(
        {"draft": draft, "expected_version": None}
    )

    result = await calc_rates.calc_rates_activate(
        {
            "revision": "r-before",
            "expected_draft_version": saved["version"],
        }
    )

    assert result["draft_archived"] is True
    assert not (rates / calc_rates.DRAFT_NAME).exists()
    archived = list((rates / calc_rates.DRAFT_ARCHIVE_DIR).glob("*.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text(encoding="utf-8")) == draft


@pytest.mark.asyncio
async def test_activate_failure_restores_draft(rates, tmp_path):
    _fake_admin(
        tmp_path,
        "import json, sys\n"
        "print(json.dumps({'error': {'code': 'InvalidRatePack', 'message': 'bad revision'}}))\n"
        "sys.exit(2)\n",
    )
    draft = {"materials": {"recover-me": 1}}
    saved = await calc_rates.calc_rates_draft_put(
        {"draft": draft, "expected_version": None}
    )

    with pytest.raises(HTTPException):
        await calc_rates.calc_rates_activate(
            {
                "revision": "broken",
                "expected_draft_version": saved["version"],
            }
        )

    assert json.loads((rates / calc_rates.DRAFT_NAME).read_text(encoding="utf-8")) == draft


@pytest.mark.asyncio
async def test_activate_rejects_a_stale_draft_version_without_archiving(
    rates, monkeypatch
):
    first = await calc_rates.calc_rates_draft_put(
        {"draft": {"materials": {"first": 1}}, "expected_version": None}
    )
    second = await calc_rates.calc_rates_draft_put(
        {
            "draft": {"materials": {"newer": 2}},
            "expected_version": first["version"],
        }
    )

    async def must_not_run(*args, **kwargs):
        raise AssertionError("stale activation reached the stateful CLI")

    monkeypatch.setattr(calc_rates, "_run_admin", must_not_run)
    with pytest.raises(HTTPException) as stale:
        await calc_rates.calc_rates_activate(
            {
                "revision": "r-before",
                "expected_draft_version": first["version"],
            }
        )

    assert stale.value.status_code == 409
    current = await calc_rates.calc_rates_draft_get()
    assert current["version"] == second["version"]
    assert current["draft"] == {"materials": {"newer": 2}}
    assert not (rates / calc_rates.DRAFT_ARCHIVE_DIR).exists()


@pytest.mark.asyncio
async def test_activate_serializes_a_concurrent_draft_write(rates, monkeypatch):
    saved = await calc_rates.calc_rates_draft_put(
        {"draft": {"materials": {"archive": 1}}, "expected_version": None}
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_activate(*args, **kwargs):
        started.set()
        await release.wait()
        return {"ok": True, "revision": "r-before"}

    monkeypatch.setattr(calc_rates, "_run_admin", slow_activate)
    activating = asyncio.create_task(
        calc_rates.calc_rates_activate(
            {
                "revision": "r-before",
                "expected_draft_version": saved["version"],
            }
        )
    )
    await started.wait()
    competing = asyncio.create_task(
        calc_rates.calc_rates_draft_put(
            {
                "draft": {"materials": {"competing": 2}},
                "expected_version": saved["version"],
            }
        )
    )
    await asyncio.sleep(0)
    assert not competing.done()
    release.set()
    assert (await activating)["revision"] == "r-before"
    with pytest.raises(HTTPException) as stale:
        await competing
    assert stale.value.status_code == 409
    assert (await calc_rates.calc_rates_draft_get())["exists"] is False
    assert len(list((rates / calc_rates.DRAFT_ARCHIVE_DIR).glob("*.json"))) == 1


# Тест поднимает настоящий подпроцесс и обязан его убить — иначе таймаут
# нечем проверить. Гвард репозитория не отличает наш же дочерний процесс от
# чужого pid, поэтому здесь используется его штатный обход.
@pytest.mark.live_system_guard_bypass
@pytest.mark.asyncio
async def test_slow_cli_does_not_hang_the_panel(rates, tmp_path, monkeypatch):
    _fake_admin(tmp_path, "import time; time.sleep(30)\n")
    monkeypatch.setattr(calc_rates, "CLI_TIMEOUT_SECONDS", 1)
    with pytest.raises(HTTPException) as exc:
        await calc_rates.calc_rates_validate({})
    assert exc.value.status_code == 504
