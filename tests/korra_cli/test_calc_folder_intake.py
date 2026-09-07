"""Real filesystem/SQLite contract for whole-folder draft intake."""
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "calculator" / "metal_calc"))
from metal_calc.errors import Conflict, FileTooLarge, InvalidIdentifier, InvalidState, NotFound
from metal_calc.folder_intake import FolderIntake
from metal_calc.registry import Registry


@pytest.fixture
def intake(tmp_path):
    root = tmp_path / "orders"
    root.mkdir()
    service = FolderIntake(root)
    yield service
    service.close()


def manifest(paths=None, folder_name="Сделка 123"):
    return {"upload_id": str(uuid4()), "folder_name": folder_name,
            "files": [{"path": path, "size": 5} for path in (paths or ["заявка.xlsx", "чертежи/деталь.pdf"])]}


def test_1001_files_publish_one_durable_draft_and_preserve_identical_contents(intake):
    request = manifest(["заявка.xlsx", *[f"чертежи/{i}.pdf" for i in range(1000)]])
    assert intake.create(request)["received"] == []
    assert intake.list()["orders"] == []
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda index: intake.upload(request["upload_id"], index, io.BytesIO(b"12345")), range(1001)))
    assert len(intake.status(request["upload_id"])["received"]) == 1001
    assert intake.list()["orders"] == []
    result = intake.complete(request["upload_id"])
    restarted = FolderIntake(intake.orders_root)
    try:
        assert restarted.complete(request["upload_id"]) == result
        assert restarted.create(request)["order_id"] == result["order_id"]
        orders = restarted.list()["orders"]
        assert len(orders) == 1
        assert orders[0]["file_count"] == 1001
        assert orders[0]["total_bytes"] == 5005
        files = restarted.detail(result["order_id"])["source_files"]
        assert len(files) == 1001
        assert files[-1]["relative_path"] == "чертежи/999.pdf"
        revision, state = Registry(intake.orders_root / "registry.db").get(result["order_id"])
        assert revision == 1
        assert state["source_files"] == []  # not calculation input before review
        assert state["status"] == "draft"
        fd, info = restarted.open_file(result["order_id"], 1000)
        with io.open(fd, "rb") as handle:
            assert handle.read() == b"12345"
        assert info["relative_path"] == files[-1]["relative_path"]
    finally:
        restarted.close()


def test_incomplete_and_different_retries_never_publish_or_overwrite(intake):
    request = manifest()
    intake.create(request)
    upload_id = request["upload_id"]
    with pytest.raises(InvalidState):
        intake.upload(upload_id, 0, io.BytesIO(b"123"))
    with pytest.raises(FileTooLarge):
        intake.upload(upload_id, 0, io.BytesIO(b"123456"))
    assert intake.status(upload_id)["received"] == []
    with pytest.raises(Conflict):
        intake.complete(upload_id)
    intake.upload(upload_id, 0, io.BytesIO(b"12345"))
    intake.upload(upload_id, 0, io.BytesIO(b"12345"))
    with pytest.raises(Conflict):
        intake.upload(upload_id, 0, io.BytesIO(b"54321"))
    changed = {**request, "files": [{"path": "other.pdf", "size": 5}]}
    with pytest.raises(Conflict):
        intake.create(changed)
    assert intake.status(upload_id)["received"] == [0]
    assert intake.list()["orders"] == []


def test_retry_recovers_file_published_before_receipt(intake):
    request = manifest(["file.pdf"])
    upload_id = request["upload_id"]
    intake.create(request)
    intake.upload(upload_id, 0, io.BytesIO(b"12345"))
    (intake.orders_root / "folders" / upload_id / "receipts" / "0.json").unlink()
    assert intake.status(upload_id)["received"] == []
    intake.upload(upload_id, 0, io.BytesIO(b"12345"))
    assert intake.complete(upload_id)["order_id"]


def test_concurrent_finalize_and_duplicate_folder_are_safe(intake):
    request = manifest(["file.pdf"])
    other = manifest(["file.pdf"])
    intake.create(request)
    intake.create(other)
    for item in (request, other):
        intake.upload(item["upload_id"], 0, io.BytesIO(b"12345"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: intake.complete(request["upload_id"]), range(2)))
    assert results[0] == results[1]
    with pytest.raises(Conflict, match="уже загружена"):
        intake.complete(other["upload_id"])
    with pytest.raises(Conflict):
        intake.create(manifest(["file.pdf"]))
    assert len(intake.list()["orders"]) == 1


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "x/../../secret", "a//b", "a/./b",
                                 "a\\b", "a\x00b", "a/\u202eb", "a/", "a/∕b"])
def test_unsafe_paths_rejected(intake, path):
    with pytest.raises(InvalidIdentifier):
        intake.create(manifest([path]))
    assert intake.list()["orders"] == []


@pytest.mark.parametrize("paths", [["a.pdf", "A.pdf"], ["a", "a/b.pdf"]])
def test_duplicate_or_conflicting_paths_rejected(intake, paths):
    with pytest.raises(Conflict):
        intake.create(manifest(paths))


def test_symlink_storage_escape_rejected(intake, tmp_path):
    request = manifest(["file.pdf"])
    intake.create(request)
    directory = intake.orders_root / "folders" / request["upload_id"] / "files"
    directory.rmdir()
    directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        intake.upload(request["upload_id"], 0, io.BytesIO(b"12345"))
    assert not (tmp_path / "0").exists()


def test_limits_and_unknown_file_rejected(intake):
    request = manifest(["file.pdf"])
    request["files"][0]["size"] = 101 * 1024 * 1024
    with pytest.raises(FileTooLarge):
        intake.create(request)
    request["files"][0]["size"] = -1
    with pytest.raises(InvalidState):
        intake.create(request)
    request["files"][0]["size"] = 5
    intake.create(request)
    with pytest.raises(InvalidIdentifier):
        intake.upload(request["upload_id"], -1, io.BytesIO(b"12345"))
    with pytest.raises(NotFound):
        intake.status(str(uuid4()))


def test_empty_placeholder_survives_complete_and_download(intake):
    request = manifest(["пустой.txt"])
    request["files"][0]["size"] = 0
    intake.create(request)
    intake.upload(request["upload_id"], 0, io.BytesIO(b""))
    result = intake.complete(request["upload_id"])
    fd, info = intake.open_file(result["order_id"], 0)
    with io.open(fd, "rb") as handle:
        assert handle.read() == b""
    assert info["bytes"] == 0


def test_nested_and_empty_directories_survive_restart(intake):
    request = manifest(["Чертежи/Корпус/деталь.pdf"])
    request["directories"] = ["Результаты/Архив", "Чертежи", "Результаты"]
    intake.create(request)
    intake.upload(request["upload_id"], 0, io.BytesIO(b"12345"))
    order_id = intake.complete(request["upload_id"])["order_id"]
    restarted = FolderIntake(intake.orders_root)
    try:
        detail = restarted.detail(order_id)
        assert detail["directories"] == ["Результаты", "Результаты/Архив", "Чертежи", "Чертежи/Корпус"]
        assert detail["file_count"] == 1
        assert restarted.create({**request, "directories": list(reversed(request["directories"]))})["order_id"] == order_id
        with pytest.raises(Conflict):
            restarted.create({**request, "directories": [*request["directories"], "Добавлено"]})
    finally:
        restarted.close()


def test_directory_only_order_has_one_durable_draft(intake):
    request = {"upload_id": str(uuid4()), "folder_name": "Будущий заказ", "files": [],
               "directories": ["Чертежи/Корпус", "Заявки"]}
    assert intake.create(request)["received"] == []
    result = intake.complete(request["upload_id"])
    detail = intake.detail(result["order_id"])
    assert detail["source_files"] == [] and detail["total_bytes"] == 0
    assert detail["directories"] == ["Заявки", "Чертежи", "Чертежи/Корпус"]
    assert intake.complete(request["upload_id"]) == result
    assert len(intake.list()["orders"]) == 1


def test_legacy_manifest_replay_and_completed_draft_infer_directories(intake):
    request = manifest(["Чертежи/Корпус/деталь.pdf"])
    intake.create(request)
    path = intake.orders_root / "folders" / request["upload_id"] / "manifest.json"
    old_manifest = json.loads(path.read_text())
    del old_manifest["directories"]
    path.write_text(json.dumps(old_manifest))
    assert intake.create(request)["received"] == []
    assert intake.create({**request, "directories": ["Чертежи", "Чертежи/Корпус"]})["received"] == []
    intake.upload(request["upload_id"], 0, io.BytesIO(b"12345"))
    order_id = intake.complete(request["upload_id"])["order_id"]
    Registry(intake.orders_root / "registry.db").mutate(
        order_id, lambda state: state["folder_intake"].pop("directories"))
    assert intake.detail(order_id)["directories"] == ["Чертежи", "Чертежи/Корпус"]


@pytest.mark.parametrize("directories", [["../secret"], ["/etc"], ["a//b"], ["x/../../y"], ["a\\b"]])
def test_unsafe_directory_paths_rejected(intake, directories):
    with pytest.raises(InvalidIdentifier):
        intake.create({**manifest(), "directories": directories})


def test_empty_root_directory_conflicts_and_directory_limits(intake, monkeypatch):
    from metal_calc import folder_intake
    with pytest.raises(InvalidState, match="полностью пуста"):
        intake.create({"upload_id": str(uuid4()), "folder_name": "Пусто", "files": []})
    with pytest.raises(InvalidState):
        intake.create({**manifest(), "directories": None})
    for directories in [["заявка.xlsx"], ["заявка.xlsx/Внутри"]]:
        with pytest.raises(Conflict):
            intake.create({**manifest(), "directories": directories})
    monkeypatch.setattr(folder_intake, "MAX_DIRECTORIES", 2)
    with pytest.raises(InvalidState):
        intake.create({**manifest(), "directories": ["A", "B", "C"]})
    with pytest.raises(InvalidState):
        intake.create({**manifest(["A/B/C/file.pdf"]), "directories": []})
