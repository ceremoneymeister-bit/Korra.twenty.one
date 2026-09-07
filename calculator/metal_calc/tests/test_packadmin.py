"""Публикация данных предприятия и защита от смешанного пака.

Методолог правит ставки сама и в любой момент. Здесь проверяется то, что
из этого следует: история неперезаписываема, откат перевалидирует, а одна
цена никогда не собирается из двух разных наборов ставок.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metal_calc.errors import Conflict, InvalidIdentifier, InvalidRatePack, PackChanged
from metal_calc.packadmin import JOURNAL, PackPublisher, make_revision, validate_pack
from metal_calc.packs2 import ACTIVE_POINTER, PipelinePackStore
from metal_calc.registry import Registry
from metal_calc.securefs import SecureRoot
from metal_calc.service2 import PipelineService

from test_pipeline_v2 import new_order, pipeline_pack

AUTHOR = "tg758050420"


@pytest.fixture
def rates(tmp_path: Path) -> Path:
    root = tmp_path / "rates2"
    root.mkdir()
    return root


@pytest.fixture
def publisher(rates: Path) -> PackPublisher:
    return PackPublisher(SecureRoot(rates, writable=True))


def _pack_bytes(**overrides: Any) -> bytes:
    pack = pipeline_pack()
    pack.update(overrides)
    return json.dumps(pack).encode("utf-8")


# ── проверка без записи ──────────────────────────────────────────────────


def test_validate_reports_sha_and_writes_nothing(rates: Path) -> None:
    result = validate_pack(_pack_bytes())
    assert result["ok"] is True
    assert len(result["sha256"]) == 64
    assert list(rates.iterdir()) == []


def test_validate_names_the_offending_row(rates: Path) -> None:
    """Сообщение обязано указывать строку — иначе его нельзя починить.

    На экране с четырьмя десятками материалов «Invalid material fields» без
    адреса означает поиск вслепую.
    """
    pack = pipeline_pack()
    pack["materials"]["steel-40x"]["rate_source"] = {"kind": "llm", "ref": "x", "as_of": "2026-08-20"}
    with pytest.raises(InvalidRatePack) as exc:
        validate_pack(json.dumps(pack).encode("utf-8"))
    assert exc.value.public_message.startswith("materials.steel-40x: ")


def test_validate_rejects_llm_as_a_source(rates: Path) -> None:
    pack = pipeline_pack()
    pack["norm_params"]["turning:steel"]["source"] = {
        "kind": "llm",
        "ref": "модель сказала",
        "as_of": "2026-08-20",
    }
    with pytest.raises(InvalidRatePack):
        validate_pack(json.dumps(pack).encode("utf-8"))


# ── публикация ───────────────────────────────────────────────────────────


def test_publish_activates_and_journals(publisher: PackPublisher, rates: Path) -> None:
    result = publisher.publish(_pack_bytes(), author=AUTHOR, note="первые прайсы")
    revision = result["revision"]
    assert (rates / f"{revision}.json").exists()

    pointer = json.loads((rates / ACTIVE_POINTER).read_text())
    assert pointer["revision"] == revision
    assert pointer["sha256"] == result["sha256"]

    entries = [json.loads(line) for line in (rates / JOURNAL).read_text().splitlines()]
    assert [entry["action"] for entry in entries] == ["publish", "activate"]
    assert entries[0]["author"] == AUTHOR
    assert entries[0]["note"] == "первые прайсы"


def test_publish_stamps_its_own_revision_name(publisher: PackPublisher) -> None:
    """Имя ревизии ставим мы, а не автор пака.

    Иначе панель могла бы записать содержимое под чужим именем — и журнал
    перестал бы соответствовать файлам.
    """
    result = publisher.publish(_pack_bytes(revision="подделка"), author=AUTHOR)
    assert result["revision"].startswith("r")
    assert result["revision"].endswith(AUTHOR)


def test_history_is_not_overwritable(publisher: PackPublisher, rates: Path) -> None:
    """Ревизия иммутабельна: запись в занятое имя обязана падать.

    Это то, что защищает вчерашние цены от ошибки в коде, а не только от
    злого умысла.
    """
    first = publisher.publish(_pack_bytes(), author=AUTHOR)
    with pytest.raises(Conflict):
        publisher.root.atomic_write(f"{first['revision']}.json", b"{}")


def test_publish_rejects_a_human_name_as_author(publisher: PackPublisher) -> None:
    # Имена людей не попадают ни в пути, ни в содержимое артефактов —
    # это ловит privacy_scan при сборке образа, но раньше должен ловить CLI.
    with pytest.raises(InvalidIdentifier):
        publisher.publish(_pack_bytes(), author="Марь Иванна")
    with pytest.raises(InvalidIdentifier):
        publisher.publish(_pack_bytes(), author="a")


def test_publish_refuses_an_invalid_pack(publisher: PackPublisher, rates: Path) -> None:
    pack = pipeline_pack()
    pack["pricing"]["vat_rate_pct"] = "300"
    with pytest.raises(InvalidRatePack):
        publisher.publish(json.dumps(pack).encode("utf-8"), author=AUTHOR)
    assert not (rates / ACTIVE_POINTER).exists()
    assert not (rates / JOURNAL).exists()


def test_no_activate_leaves_the_pointer_alone(publisher: PackPublisher) -> None:
    first = publisher.publish(_pack_bytes(), author=AUTHOR)
    second = publisher.publish(_pack_bytes(), author=AUTHOR, activate=False)
    assert second["revision"] != first["revision"]
    assert publisher.active_revision() == first["revision"]


def test_revision_names_sort_as_history() -> None:
    early = make_revision(AUTHOR, now="2026-08-27T09:15:00Z")
    late = make_revision(AUTHOR, now="2026-08-27T11:42:10Z")
    assert early < late
    assert early == f"r20260827-091500-{AUTHOR}"


# ── откат ────────────────────────────────────────────────────────────────


def test_activate_rolls_back_to_an_earlier_revision(publisher: PackPublisher) -> None:
    first = publisher.publish(_pack_bytes(), author=AUTHOR)
    pack = pipeline_pack()
    pack["machines"]["turning"]["rate_rub_per_hour"] = "9000"
    second = publisher.publish(json.dumps(pack).encode("utf-8"), author=AUTHOR)
    assert publisher.active_revision() == second["revision"]

    publisher.activate(first["revision"], author=AUTHOR)
    assert publisher.active_revision() == first["revision"]
    assert [e["action"] for e in publisher.journal()][-1] == "activate"


def test_activate_revalidates_the_stored_file(publisher: PackPublisher, rates: Path) -> None:
    """Откатывают и на старую ревизию, а её файл писали прежние правила.

    Молча вернуть в бой то, что сегодня невалидно, — худший исход отката.
    """
    result = publisher.publish(_pack_bytes(), author=AUTHOR)
    broken = json.loads((rates / f"{result['revision']}.json").read_text())
    broken["pricing"]["margin_basis"] = "on_whim"
    (rates / f"{result['revision']}.json").write_text(json.dumps(broken))
    with pytest.raises(InvalidRatePack):
        publisher.activate(result["revision"], author=AUTHOR)


def test_activate_reports_a_missing_revision(publisher: PackPublisher) -> None:
    with pytest.raises(InvalidRatePack):
        publisher.activate("r20260101-000000-nobody", author=AUTHOR)


# ── история и сверка целостности ─────────────────────────────────────────


def test_revisions_list_marks_the_active_one(publisher: PackPublisher) -> None:
    first = publisher.publish(_pack_bytes(), author=AUTHOR, note="раз")
    second = publisher.publish(_pack_bytes(), author=AUTHOR, note="два")
    listing = publisher.revisions()
    assert listing["active"] == second["revision"]
    assert [item["revision"] for item in listing["revisions"]] == [
        second["revision"],
        first["revision"],
    ]
    assert listing["revisions"][0]["note"] == "два"
    assert all(item["journaled"] for item in listing["revisions"])


def test_revisions_hide_service_files(publisher: PackPublisher) -> None:
    publisher.publish(_pack_bytes(), author=AUTHOR)
    names = {item["revision"] for item in publisher.revisions()["revisions"]}
    assert not any(name.startswith("_") for name in names)


def test_verify_catches_a_write_that_bypassed_publishing(
    publisher: PackPublisher, rates: Path
) -> None:
    """Каталог ставок стал доступен на запись — журнал это и компенсирует.

    Правка цен мимо CLI обязана быть заметной, иначе rw-доступ мы отдали
    даром.
    """
    result = publisher.publish(_pack_bytes(), author=AUTHOR)
    assert publisher.verify_active()["ok"] is True

    tampered = json.loads((rates / f"{result['revision']}.json").read_text())
    tampered["materials"]["steel-40x"]["rate_rub_per_kg"] = "1"
    (rates / f"{result['revision']}.json").write_text(json.dumps(tampered))

    verdict = publisher.verify_active()
    assert verdict["ok"] is False
    assert verdict["reason"] == "active pack content differs from the journal"


def test_verify_is_quiet_when_nothing_is_published(publisher: PackPublisher) -> None:
    assert publisher.verify_active() == {"ok": True, "active": None, "reason": "no active pack"}


# ── read-only сторона ────────────────────────────────────────────────────


def test_store_reads_the_active_pointer(publisher: PackPublisher, rates: Path) -> None:
    result = publisher.publish(_pack_bytes(), author=AUTHOR)
    store = PipelinePackStore(SecureRoot(rates, writable=False))
    assert store.active_revision() == result["revision"]
    assert store.load_active().sha256 == result["sha256"]


def test_store_says_data_is_not_set_up_yet(rates: Path) -> None:
    """Нет указателя — это «ещё не заполнено», а не «сломалось».

    Сообщение читает агент и пересказывает человеку; оно должно вести его в
    экран ввода, а не в поддержку.
    """
    store = PipelinePackStore(SecureRoot(rates, writable=False))
    with pytest.raises(InvalidRatePack) as exc:
        store.load_active()
    assert "не заведены" in exc.value.public_message


def test_publisher_refuses_a_read_only_root(rates: Path) -> None:
    with pytest.raises(InvalidRatePack):
        PackPublisher(SecureRoot(rates, writable=False))


# ── смешанный пак ────────────────────────────────────────────────────────


@pytest.fixture
def live(rates: Path, tmp_path: Path) -> tuple[PipelineService, PackPublisher]:
    publisher = PackPublisher(SecureRoot(rates, writable=True))
    publisher.publish(_pack_bytes(), author=AUTHOR)
    registry = Registry(tmp_path / "orders2" / "registry.db")
    pipeline = PipelineService(
        registry, PipelinePackStore(SecureRoot(rates, writable=False))
    )
    return pipeline, publisher


def _republish_with_new_machine_rate(publisher: PackPublisher, rate: str) -> None:
    pack = pipeline_pack()
    pack["machines"]["turning"]["rate_rub_per_hour"] = rate
    publisher.publish(json.dumps(pack).encode("utf-8"), author=AUTHOR)


def _approved_blank(pipeline: PipelineService, order_id: str) -> int:
    revision = new_order(pipeline, order_id)
    blank = pipeline.blank_cost(
        order_id,
        revision,
        "steel-40x",
        1,
        "12.5",
        "total",
        "масса из чертежа",
        [{"op_code": "bandsaw", "cuts": 4, "note": "торцовка"}],
    )
    return pipeline.stage_approve(order_id, blank["revision"], "blank", "методолог")["revision"]


def test_route_refuses_a_blank_priced_by_another_pack(live) -> None:
    pipeline, publisher = live
    revision = _approved_blank(pipeline, "ord-mix-1")
    _republish_with_new_machine_rate(publisher, "9000")
    with pytest.raises(PackChanged) as exc:
        pipeline.route_propose(
            "ord-mix-1", revision, [{"seq": 1, "op_code": "turning", "note": "точение"}]
        )
    assert "Заготовка" in exc.value.public_message


def test_quote_refuses_to_mix_two_packs(live) -> None:
    """Главная защита: суммы по старым ставкам + маржа по новым = чужая цена.

    Именно эта смесь ушла бы заказчику молча, и именно она не соответствует
    ни одному опубликованному набору данных.
    """
    pipeline, publisher = live
    revision = _approved_blank(pipeline, "ord-mix-2")
    route = pipeline.route_propose(
        "ord-mix-2", revision, [{"seq": 1, "op_code": "turning", "note": "точение"}]
    )
    ok = pipeline.stage_approve("ord-mix-2", route["revision"], "route", "методолог")
    time_result = pipeline.time_calc(
        "ord-mix-2",
        ok["revision"],
        "steel-40x",
        [
            {
                "route_seq": 1,
                "quantity": 1,
                "batch": 1,
                "params": {"diameter_mm": 50, "length_mm": 40, "stock_mm": 2},
            }
        ],
    )
    approved = pipeline.stage_approve("ord-mix-2", time_result["revision"], "time", "методолог")

    _republish_with_new_machine_rate(publisher, "9000")
    with pytest.raises(PackChanged):
        pipeline.quote_build("ord-mix-2", approved["revision"])


def test_time_refuses_a_route_priced_by_another_pack(live) -> None:
    pipeline, publisher = live
    revision = _approved_blank(pipeline, "ord-mix-3")
    route = pipeline.route_propose(
        "ord-mix-3", revision, [{"seq": 1, "op_code": "turning", "note": "точение"}]
    )
    ok = pipeline.stage_approve("ord-mix-3", route["revision"], "route", "методолог")
    _republish_with_new_machine_rate(publisher, "9000")
    with pytest.raises(PackChanged):
        pipeline.time_calc(
            "ord-mix-3",
            ok["revision"],
            "steel-40x",
            [
                {
                    "route_seq": 1,
                    "quantity": 1,
                    "batch": 1,
                    "params": {"diameter_mm": 50, "length_mm": 40, "stock_mm": 2},
                }
            ],
        )


def test_republishing_identical_content_is_not_a_change(live) -> None:
    """Сверяем содержимое, а не имя ревизии.

    Публикация без правок меняет имя, но не байты; ронять на этом живую
    работу — значит наказывать за нажатие «Сохранить».
    """
    pipeline, publisher = live
    revision = _approved_blank(pipeline, "ord-mix-4")
    publisher.publish(_pack_bytes(), author=AUTHOR)
    route = pipeline.route_propose(
        "ord-mix-4", revision, [{"seq": 1, "op_code": "turning", "note": "точение"}]
    )
    assert route["stage"] == "route"


def test_status_names_the_stale_stages(live) -> None:
    pipeline, publisher = live
    _approved_blank(pipeline, "ord-mix-5")
    _republish_with_new_machine_rate(publisher, "9000")
    status = pipeline.pipeline_status("ord-mix-5")
    assert status["pack"]["stale_stages"] == ["blank"]
    assert status["pack"]["active_revision"] == publisher.active_revision()


def test_status_survives_missing_company_data(rates: Path, tmp_path: Path) -> None:
    registry = Registry(tmp_path / "orders3" / "registry.db")
    pipeline = PipelineService(registry, PipelinePackStore(SecureRoot(rates, writable=False)))
    new_order(pipeline, "ord-empty")
    status = pipeline.pipeline_status("ord-empty")
    assert status["pack"]["active_revision"] is None
    assert "не заведены" in status["pack"]["unavailable"]
