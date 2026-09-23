"""Сессия загрузки: манифест, части, публикация, дедупликация, уборка (K21-059).

Контракт описан в A2-files-implementation-plan.md §1. Тесты работают через
реальный HTTP поверх ``web_server.app``: путь запроса, периметр managed-files и
права на диске проверяются вместе, а не по отдельности.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from korra_cli import web_server as server
from korra_cli.web_routers import uploads


@pytest.fixture
def panel(tmp_path, monkeypatch):
    """Флотовая панель: один общий ``workspace`` и включённый файловый экран."""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("KORRA_HOME", str(root))
    monkeypatch.setenv("KORRA_UI_MODE", "fleet")
    monkeypatch.setattr(server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(server.app.state, "bound_host", None, raising=False)
    client = TestClient(server.app, raise_server_exceptions=False)
    try:
        client.headers[server._SESSION_HEADER_NAME] = server._SESSION_TOKEN
        workspace = Path(client.get("/api/files").json()["path"])
        assert workspace == root / "workspace"
        yield client, root, workspace
    finally:
        client.close()


def _manifest(**extra):
    body = {"upload_id": str(uuid.uuid4()), "origin": "chat", "files": []}
    body.update(extra)
    return body


def _entry(path: str, payload: bytes, **extra):
    return {"path": path, "size": len(payload), **extra}


@pytest.mark.parametrize("ending", ["disconnect", "short", "cancel"])
def test_interrupted_part_rolls_back_and_can_resume(panel, ending):
    from fastapi import HTTPException
    from starlette.requests import Request

    client, _, _ = panel
    manifest = _manifest(files=[_entry("data.txt", b"abcdef")])
    assert client.post("/api/uploads", json=manifest).status_code == 201
    upload_id = manifest["upload_id"]
    assert _put(client, upload_id, 0, b"ab").status_code == 200
    messages = iter([{"type": "http.request", "body": b"cd", "more_body": ending != "short"},
                     {"type": "http.disconnect"}])

    async def receive():
        message = next(messages)
        if ending == "cancel" and message["type"] == "http.disconnect":
            raise asyncio.CancelledError()
        return message

    request = Request({"type": "http", "app": server.app, "method": "PUT", "path": "/",
                       "headers": [(b"content-length", b"4")], "server": ("testserver", 80)}, receive)
    expected = asyncio.CancelledError if ending == "cancel" else HTTPException
    with pytest.raises(expected) as caught:
        asyncio.run(uploads.upload_part(upload_id, 0, request, offset=2))
    if ending != "cancel":
        assert caught.value.status_code == (408 if ending == "disconnect" else 400)
    status = client.get(f"/api/uploads/{upload_id}").json()
    assert status["received"] == [{"index": 0, "bytes": 2, "complete": False}]
    assert _put(client, upload_id, 0, b"cdef", offset=2).status_code == 200
    result = client.post(f"/api/uploads/{upload_id}/complete", json={})
    assert result.status_code == 200, result.text
    assert Path(result.json()["files"][0]["path"]).read_bytes() == b"abcdef"
    status = client.get(f"/api/uploads/{upload_id}").json()
    assert status["received"] == [{"index": 0, "bytes": 6, "complete": True}]


def test_invalid_unicode_manifest_returns_controlled_error(panel):
    client, _, _ = panel
    manifest = _manifest(files=[_entry("bad\ud800.txt", b"x")])
    import json
    response = client.post("/api/uploads", content=json.dumps(manifest), headers={"Content-Type": "application/json"})
    assert response.status_code == 400


def test_disk_full_during_part_preserves_acknowledged_bytes(panel, monkeypatch):
    import errno
    client, _, _ = panel
    manifest = _manifest(files=[_entry("data.txt", b"abcdef")])
    assert client.post("/api/uploads", json=manifest).status_code == 201
    upload_id = manifest["upload_id"]
    assert _put(client, upload_id, 0, b"ab").status_code == 200
    write = uploads._write_chunk
    def full(fd, chunk):
        write(fd, chunk[:1])
        raise OSError(errno.ENOSPC, "synthetic full disk")
    monkeypatch.setattr(uploads, "_write_chunk", full)
    response = _put(client, upload_id, 0, b"cdef", offset=2)
    assert response.status_code == 507
    assert client.get(f"/api/uploads/{upload_id}").json()["received"][0]["bytes"] == 2
    monkeypatch.setattr(uploads, "_write_chunk", write)
    assert _put(client, upload_id, 0, b"cdef", offset=2).status_code == 200
    result = client.post(f"/api/uploads/{upload_id}/complete")
    assert Path(result.json()["files"][0]["path"]).read_bytes() == b"abcdef"


def test_replace_preserves_edits_made_while_upload_is_in_progress(panel):
    client, _, workspace = panel
    target = workspace / "report.txt"
    target.write_bytes(b"before")
    manifest = _manifest(origin="files", target={"kind": "path", "path": str(workspace)},
                         on_conflict="replace", files=[_entry("report.txt", b"upload")])
    assert client.post("/api/uploads", json=manifest).status_code == 201
    assert _put(client, manifest["upload_id"], 0, b"upload").status_code == 200
    target.write_bytes(b"owner-edit")
    result = client.post(f"/api/uploads/{manifest['upload_id']}/complete", json={})
    assert result.status_code == 409
    assert target.read_bytes() == b"owner-edit"


def _put(client, upload_id, index, payload, offset=0):
    return client.put(
        f"/api/uploads/{upload_id}/files/{index}",
        params={"offset": offset},
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )


def _send(client, manifest, payloads):
    created = client.post("/api/uploads", json=manifest)
    assert created.status_code == 201, created.text
    for index, payload in enumerate(payloads):
        assert _put(client, manifest["upload_id"], index, payload).status_code == 200
    return client.post(f"/api/uploads/{manifest['upload_id']}/complete")


# --- create ---------------------------------------------------------------


@pytest.mark.parametrize("origin", ["files", "chat"])
def test_empty_named_folder_is_published_once_without_files(panel, origin):
    client, _, workspace = panel
    manifest = _manifest(origin=origin, name="Пустая", directories=[],
                         target={"kind": "path", "path": str(workspace)})
    result = _send(client, manifest, [])
    assert result.status_code == 200, result.text
    folder = Path(result.json()["folder"]["path"])
    assert folder.name == "Пустая"
    assert folder.is_dir() and list(folder.iterdir()) == []
    assert result.json()["folder"]["file_count"] == 0
    assert result.json()["files"] == []
    assert client.post(f"/api/uploads/{manifest['upload_id']}/complete").json() == result.json()
    assert client.post("/api/uploads", json=_manifest()).status_code == 400


def test_create_is_idempotent_and_rejects_a_changed_file_list(panel):
    client, _root, _workspace = panel
    manifest = _manifest(files=[_entry("смета.pdf", b"a" * 10)])

    created = client.post("/api/uploads", json=manifest)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["published"] is False
    assert body["received"] == [{"index": 0, "bytes": 0, "complete": False}]
    assert body["limits"]["part_bytes"] == uploads.PART_BYTES

    repeated = client.post("/api/uploads", json=manifest)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["upload_id"] == manifest["upload_id"]

    changed = dict(manifest, files=[_entry("другая.pdf", b"a" * 10)])
    conflict = client.post("/api/uploads", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "Эта загрузка уже имеет другой список файлов"


def test_create_refuses_unsafe_names_and_oversized_sets(panel):
    client, _root, workspace = panel
    cases = [
        (_manifest(files=[_entry("../побег.pdf", b"x")]), 400),
        (_manifest(files=[_entry("a/../b.pdf", b"x")]), 400),
        (_manifest(files=[_entry(".env", b"x")]), 400),
        (_manifest(files=[_entry("установщик.sh", b"x")]), 400),
        (_manifest(files=[{"path": "видео.mp4", "size": uploads.MAX_FILE_BYTES + 1}]), 413),
        (_manifest(files=[{"path": f"f{i}.txt", "size": 1} for i in range(uploads.MAX_FILES + 1)]), 413),
        (_manifest(origin="files", target={"kind": "path", "path": "/etc"},
                   files=[_entry("x.txt", b"x")]), 403),
        (_manifest(origin="files", target={"kind": "path", "path": str(workspace / ".trash")},
                   files=[_entry("x.txt", b"x")]), 403),
    ]
    for manifest, expected in cases:
        response = client.post("/api/uploads", json=manifest)
        assert response.status_code == expected, (manifest["files"][:1], response.text)

    # «Файлы» не ограничивают расширения — там владелец кладёт что угодно.
    allowed = _manifest(origin="files", target={"kind": "path", "path": str(workspace)},
                        files=[_entry("установщик.sh", b"x")])
    assert client.post("/api/uploads", json=allowed).status_code == 201


def test_create_reports_a_full_disk_before_the_first_part(panel, monkeypatch):
    client, _root, _workspace = panel
    import shutil

    usage = shutil.disk_usage(".")
    monkeypatch.setattr(
        uploads.shutil, "disk_usage", lambda _path: usage._replace(free=1024)
    )
    response = client.post(
        "/api/uploads",
        json=_manifest(files=[{"path": f"{i}.bin", "size": 1024 ** 3} for i in range(5)]),
    )
    assert response.status_code == 507
    assert "не хватает места" in response.json()["detail"]


# --- части ----------------------------------------------------------------


def test_parts_assemble_the_file_and_a_replayed_part_changes_nothing(panel):
    client, _root, _workspace = panel
    payload = os.urandom(9000)
    manifest = _manifest(files=[_entry("данные.bin", payload)])
    assert client.post("/api/uploads", json=manifest).status_code == 201

    first = _put(client, manifest["upload_id"], 0, payload[:4000])
    assert first.status_code == 200
    assert first.json() == {"index": 0, "bytes": 4000, "complete": False}

    # Ретрай после неоднозначного исхода: байты уже на месте, файл не портится.
    replay = _put(client, manifest["upload_id"], 0, payload[:4000])
    assert replay.status_code == 200
    assert replay.json()["bytes"] == 4000

    tail = _put(client, manifest["upload_id"], 0, payload[4000:], offset=4000)
    assert tail.status_code == 200
    assert tail.json() == {"index": 0, "bytes": 9000, "complete": True}

    status = client.get(f"/api/uploads/{manifest['upload_id']}").json()
    assert status["received"] == [{"index": 0, "bytes": 9000, "complete": True}]


def test_a_gap_in_the_offset_returns_the_current_size(panel):
    client, _root, _workspace = panel
    manifest = _manifest(files=[_entry("данные.bin", b"z" * 100)])
    client.post("/api/uploads", json=manifest)
    assert _put(client, manifest["upload_id"], 0, b"z" * 40).status_code == 200

    ahead = _put(client, manifest["upload_id"], 0, b"z" * 10, offset=60)
    assert ahead.status_code == 409
    assert ahead.json()["bytes"] == 40

    resumed = _put(client, manifest["upload_id"], 0, b"z" * 60, offset=40)
    assert resumed.status_code == 200
    assert resumed.json()["complete"] is True


def test_oversized_parts_and_bodies_are_refused(panel):
    client, _root, _workspace = panel
    manifest = _manifest(files=[_entry("данные.bin", b"z" * 10)])
    client.post("/api/uploads", json=manifest)

    too_big = _put(client, manifest["upload_id"], 0, b"z" * (uploads.PART_BYTES + 1))
    assert too_big.status_code == 413

    over_declared = _put(client, manifest["upload_id"], 0, b"z" * 11)
    assert over_declared.status_code == 413

    missing = _put(client, str(uuid.uuid4()), 0, b"z")
    assert missing.status_code == 404
    assert _put(client, manifest["upload_id"], 7, b"z").status_code == 404


def test_part_writes_never_run_on_the_event_loop(panel, monkeypatch):
    """Синхронная запись в loop однажды уже дала «вечный скелет» кабинета."""
    client, _root, _workspace = panel
    seen: list[str] = []
    original = uploads._write_chunk

    def probe(fd, chunk):
        try:
            asyncio.get_running_loop()
            seen.append("loop")
        except RuntimeError:
            seen.append("worker")
        return original(fd, chunk)

    monkeypatch.setattr(uploads, "_write_chunk", probe)
    manifest = _manifest(files=[_entry("данные.bin", b"z" * 2048)])
    client.post("/api/uploads", json=manifest)
    assert _put(client, manifest["upload_id"], 0, b"z" * 2048).status_code == 200
    assert seen and set(seen) == {"worker"}


# --- публикация -----------------------------------------------------------


def test_complete_publishes_a_package_folder_and_repeats_return_the_same_result(panel):
    client, _root, workspace = panel
    payloads = [b"first", b"second"]
    manifest = _manifest(files=[_entry("а.txt", payloads[0]), _entry("б.txt", payloads[1])])

    done = _send(client, manifest, payloads)
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["published"] is True
    assert result["folder"] is None
    paths = [Path(item["path"]) for item in result["files"]]
    assert [path.read_bytes() for path in paths] == payloads
    assert all(path.parent == paths[0].parent for path in paths)
    assert paths[0].parent.parent.parent == workspace / "client" / "inbox"
    assert oct(paths[0].stat().st_mode)[-3:] == "644"
    assert oct(paths[0].parent.stat().st_mode)[-3:] == "755"
    assert result["files"][0]["reader"] == "read_file"

    repeated = client.post(f"/api/uploads/{manifest['upload_id']}/complete")
    assert repeated.status_code == 200
    assert repeated.json() == result
    assert len(list(paths[0].parent.iterdir())) == 2


def test_complete_reports_the_files_it_is_still_missing(panel):
    client, _root, _workspace = panel
    manifest = _manifest(files=[_entry("а.txt", b"aa"), _entry("б.txt", b"bb")])
    client.post("/api/uploads", json=manifest)
    _put(client, manifest["upload_id"], 0, b"aa")

    response = client.post(f"/api/uploads/{manifest['upload_id']}/complete")
    assert response.status_code == 409
    assert response.json()["missing"] == [1]


def test_complete_honours_the_conflict_policy_of_the_files_screen(panel):
    client, _root, workspace = panel
    (workspace / "Проекты").mkdir()
    existing = workspace / "Проекты" / "смета.txt"
    existing.write_bytes("старое".encode())
    target = {"kind": "path", "path": str(workspace / "Проекты")}

    skipped = _send(client, _manifest(origin="files", target=target, on_conflict="skip",
                                      files=[_entry("смета.txt", "новое".encode())]), ["новое".encode()])
    assert skipped.status_code == 200
    assert skipped.json()["skipped"] == [0]
    assert skipped.json()["files"][0]["skipped"] is True
    assert existing.read_bytes() == "старое".encode()

    copied = _send(client, _manifest(origin="files", target=target, on_conflict="copy",
                                     files=[_entry("смета.txt", "копия".encode())]), ["копия".encode()])
    assert copied.status_code == 200
    assert Path(copied.json()["files"][0]["path"]).name == "смета (2).txt"
    assert existing.read_bytes() == "старое".encode()

    replaced = _send(client, _manifest(origin="files", target=target, on_conflict="replace",
                                       files=[_entry("смета.txt", "замена".encode())]), ["замена".encode()])
    assert replaced.status_code == 200
    assert existing.read_bytes() == "замена".encode()


def test_folder_upload_keeps_nesting_and_empty_directories(panel):
    client, _root, workspace = panel
    manifest = _manifest(
        origin="files",
        target={"kind": "path", "path": str(workspace)},
        name="Референсы",
        directories=["фото", "пустая"],
        files=[_entry("фото/кадр.jpg", b"jpeg")],
    )
    done = _send(client, manifest, [b"jpeg"])
    assert done.status_code == 200, done.text
    folder = done.json()["folder"]
    assert folder["name"] == "Референсы"
    assert folder["file_count"] == 1
    assert (workspace / "Референсы" / "пустая").is_dir()
    assert (workspace / "Референсы" / "фото" / "кадр.jpg").read_bytes() == b"jpeg"


def test_chat_packages_deduplicate_by_content(panel):
    client, _root, workspace = panel
    payload = b"IMG data" * 100
    first = _send(client, _manifest(files=[_entry("IMG_2581.jpg", payload)]), [payload])
    original = Path(first.json()["files"][0]["path"])
    assert first.json()["files"][0]["deduplicated"] is False

    second = _send(client, _manifest(files=[_entry("IMG_2581.jpg", payload)]), [payload])
    entry = second.json()["files"][0]
    assert entry["deduplicated"] is True
    assert Path(entry["path"]) == original
    assert len(list((workspace / "client" / "inbox").rglob("IMG_2581.jpg"))) == 1


def test_organized_files_jpg_upload_and_chat_jpg_keep_separate_paths(panel):
    client, _root, workspace = panel
    payload = b"\xff\xd8\xffjpeg-test"
    shared = workspace / "shared"
    files_result = _send(client, _manifest(
        origin="files", target={"kind": "path", "path": str(shared)},
        on_conflict="copy", files=[_entry("photo.jpg", payload)],
    ), [payload])
    assert files_result.status_code == 200, files_result.text
    assert Path(files_result.json()["files"][0]["path"]) == shared / "photo.jpg"
    assert (shared / "photo.jpg").read_bytes() == payload

    chat_result = _send(client, _manifest(files=[_entry("photo.jpg", payload)]), [payload])
    assert chat_result.status_code == 200, chat_result.text
    chat_path = Path(chat_result.json()["files"][0]["path"])
    assert workspace / "client" / "inbox" in chat_path.parents
    assert chat_path.read_bytes() == payload
    assert (shared / "photo.jpg").read_bytes() == payload


def test_client_side_hash_lets_the_browser_skip_a_known_file(panel):
    client, _root, _workspace = panel
    import hashlib

    payload = "повтор".encode() * 50
    digest = hashlib.sha256(payload).hexdigest()
    first = _send(client, _manifest(files=[_entry("копия.txt", payload, sha256=digest)]), [payload])
    known = first.json()["files"][0]["path"]

    manifest = _manifest(files=[_entry("копия.txt", payload, sha256=digest)])
    created = client.post("/api/uploads", json=manifest)
    assert created.status_code == 201
    assert created.json()["already_present"] == [0]
    done = client.post(f"/api/uploads/{manifest['upload_id']}/complete")
    assert done.status_code == 200, done.text
    assert done.json()["files"][0]["path"] == known
    assert done.json()["files"][0]["deduplicated"] is True


def test_cancelled_files_are_excluded_from_the_package(panel):
    client, _root, _workspace = panel
    manifest = _manifest(files=[_entry("а.txt", b"aa"), _entry("б.txt", b"bb")])
    client.post("/api/uploads", json=manifest)
    _put(client, manifest["upload_id"], 0, b"aa")

    done = client.post(f"/api/uploads/{manifest['upload_id']}/complete", json={"exclude": [1]})
    assert done.status_code == 200, done.text
    assert [item["index"] for item in done.json()["files"]] == [0]
    assert done.json()["excluded"] == [1]


def test_a_published_session_refuses_more_parts_and_cancellation(panel):
    client, _root, _workspace = panel
    manifest = _manifest(files=[_entry("а.txt", b"aa")])
    assert _send(client, manifest, [b"aa"]).status_code == 200

    assert _put(client, manifest["upload_id"], 0, b"aa").status_code == 410
    assert client.delete(f"/api/uploads/{manifest['upload_id']}").status_code == 409

    fresh = _manifest(files=[_entry("а.txt", b"aa")])
    client.post("/api/uploads", json=fresh)
    assert client.delete(f"/api/uploads/{fresh['upload_id']}").status_code == 204
    assert client.get(f"/api/uploads/{fresh['upload_id']}").status_code == 404


# --- служебные каталоги и уборка ------------------------------------------


def test_staging_and_the_dedup_index_stay_invisible_to_the_files_api(panel):
    client, _root, workspace = panel
    assert _send(client, _manifest(files=[_entry("б.txt", b"bb")]), [b"bb"]).status_code == 200
    manifest = _manifest(files=[_entry("а.txt", b"aa")])
    client.post("/api/uploads", json=manifest)
    _put(client, manifest["upload_id"], 0, b"aa")

    listing = client.get("/api/files", params={"path": str(workspace)}).json()
    assert ".uploads" not in [entry["name"] for entry in listing["entries"]]
    index = workspace / "client" / "inbox" / ".index"
    assert (index / "sha256.jsonl").is_file()
    inbox = client.get("/api/files", params={"path": str(index.parent)}).json()
    assert ".index" not in [entry["name"] for entry in inbox["entries"]]
    for folder in (workspace / ".uploads", index):
        denied = client.get("/api/files", params={"path": str(folder)})
        assert denied.status_code == 403, denied.text
    part = workspace / ".uploads" / manifest["upload_id"] / "files" / "0.part"
    assert part.is_file()
    assert client.get("/api/files/attachment", params={"path": str(part)}).status_code == 403


def test_abandoned_sessions_are_swept_by_ttl(panel):
    client, _root, workspace = panel
    stale = _manifest(files=[_entry("а.txt", b"aa")])
    client.post("/api/uploads", json=stale)
    published = _manifest(files=[_entry("б.txt", b"bb")])
    _send(client, published, [b"bb"])
    fresh = _manifest(files=[_entry("в.txt", b"cc")])
    client.post("/api/uploads", json=fresh)

    staging = workspace / ".uploads"
    old = time.time() - 8 * 24 * 3600
    for path in (staging / stale["upload_id"]).rglob("*"):
        os.utime(path, (old, old))
    os.utime(staging / stale["upload_id"], (old, old))
    expired = time.time() - 25 * 3600
    for path in (staging / published["upload_id"]).rglob("*"):
        os.utime(path, (expired, expired))
    os.utime(staging / published["upload_id"], (expired, expired))

    uploads.sweep_staging(staging, force=True)
    assert not (staging / stale["upload_id"]).exists()
    assert not (staging / published["upload_id"]).exists()
    assert (staging / fresh["upload_id"]).exists()


def test_every_session_route_needs_the_panel_session(panel):
    client, _root, _workspace = panel
    manifest = _manifest(files=[_entry("а.txt", b"aa")])
    client.post("/api/uploads", json=manifest)
    upload_id = manifest["upload_id"]
    client.headers.clear()
    calls = [
        client.post("/api/uploads", json=_manifest(files=[_entry("б.txt", b"bb")])),
        client.get(f"/api/uploads/{upload_id}"),
        _put(client, upload_id, 0, b"aa"),
        client.post(f"/api/uploads/{upload_id}/complete"),
        client.delete(f"/api/uploads/{upload_id}"),
    ]
    assert [response.status_code for response in calls] == [401] * 5


def test_the_sweep_runs_at_most_once_per_interval(panel):
    client, _root, workspace = panel
    staging = workspace / ".uploads"
    staging.mkdir(parents=True, exist_ok=True)
    assert uploads.sweep_staging(staging, force=True) is True
    assert uploads.sweep_staging(staging) is False


# --- совместимость --------------------------------------------------------


def test_legacy_chat_upload_lands_inside_a_package_folder(panel):
    client, _root, workspace = panel
    data = "Проверка: сумма 2145".encode()
    response = client.post("/api/chat/upload", files={"file": ("Данные.txt", data)})
    assert response.status_code == 200, response.text
    payload = response.json()
    target = Path(payload["path"])
    assert target.read_bytes() == data
    assert target.name == "Данные.txt"
    assert target.parent.parent.parent == workspace / "client" / "inbox"
    assert payload["reader"] == "read_file"
    assert payload["kind"] == "txt"
    assert oct(target.stat().st_mode)[-3:] == "644"


def test_upload_stream_publishes_a_world_readable_file(panel):
    client, _root, workspace = panel
    response = client.post(
        "/api/files/upload-stream",
        files={"file": ("отчёт.txt", b"data")},
        data={"path": str(workspace / "отчёт.txt")},
    )
    assert response.status_code == 200, response.text
    assert oct((workspace / "отчёт.txt").stat().st_mode)[-3:] == "644"


def test_complete_resumes_after_a_failure_during_publication(panel, monkeypatch):
    client, _root, workspace = panel
    manifest = _manifest(origin="files", target={"kind": "path", "path": str(workspace)},
                         on_conflict="copy", files=[_entry("a.txt", b"aa"), _entry("b.txt", b"bb")])
    assert client.post("/api/uploads", json=manifest).status_code == 201
    for index, data in enumerate([b"aa", b"bb"]):
        assert _put(client, manifest["upload_id"], index, data).status_code == 200
    publish = uploads._link_or_move
    def fail_second(part, destination):
        if destination.name == "b.txt":
            raise OSError("synthetic interrupted completion")
        return publish(part, destination)
    monkeypatch.setattr(uploads, "_link_or_move", fail_second)
    assert client.post(f"/api/uploads/{manifest['upload_id']}/complete").status_code == 500
    monkeypatch.setattr(uploads, "_link_or_move", publish)
    resumed = client.post(f"/api/uploads/{manifest['upload_id']}/complete")
    assert resumed.status_code == 200, resumed.text
    assert [Path(item["path"]).read_bytes() for item in resumed.json()["files"]] == [b"aa", b"bb"]
    assert sorted(p.name for p in workspace.glob("*.txt")) == ["a.txt", "b.txt"]
    assert client.post(f"/api/uploads/{manifest['upload_id']}/complete").json() == resumed.json()


def test_publish_revalidates_a_target_replaced_with_a_symlink(panel):
    client, root, workspace = panel
    target = workspace / "target"
    target.mkdir()
    manifest = _manifest(origin="files", target={"kind": "path", "path": str(target)},
                         files=[_entry("escape.txt", b"secret")])
    assert client.post("/api/uploads", json=manifest).status_code == 201
    assert _put(client, manifest["upload_id"], 0, b"secret").status_code == 200
    target.rmdir()
    target.symlink_to(root, target_is_directory=True)
    response = client.post(f"/api/uploads/{manifest['upload_id']}/complete")
    assert response.status_code == 403, response.text
    assert not (root / "escape.txt").exists()


def test_dedup_does_not_reuse_a_file_modified_without_changing_size(panel):
    import hashlib
    client, _root, _workspace = panel
    payload = b"original"
    digest = hashlib.sha256(payload).hexdigest()
    first = _send(client, _manifest(files=[_entry("a.txt", payload, sha256=digest)]), [payload])
    source = Path(first.json()["files"][0]["path"])
    source.write_bytes(b"modified")
    fresh = _manifest(files=[_entry("b.txt", payload, sha256=digest)])
    created = client.post("/api/uploads", json=fresh)
    assert created.json()["already_present"] == []
    assert _put(client, fresh["upload_id"], 0, payload).status_code == 200
    result = client.post(f"/api/uploads/{fresh['upload_id']}/complete")
    assert result.status_code == 200
    assert Path(result.json()["files"][0]["path"]).read_bytes() == payload
    assert source.read_bytes() == b"modified"


def test_chat_folder_contains_every_uploaded_file_even_when_contents_match(panel):
    client, _root, _workspace = panel
    count = uploads.CHAT_FOLDER_THRESHOLD + 1
    result = _send(client, _manifest(files=[_entry(f"f{i}.txt", b"same") for i in range(count)]),
                   [b"same"] * count)
    assert result.status_code == 200, result.text
    folder = Path(result.json()["folder"]["path"])
    assert {p.name for p in folder.iterdir()} == {f"f{i}.txt" for i in range(count)}


def test_manifest_body_is_bounded_before_json_parsing(panel, monkeypatch):
    client, _root, _workspace = panel
    monkeypatch.setattr(uploads, "MAX_MANIFEST_BYTES", 64)
    response = client.post("/api/uploads", content=b" " * 65,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413
