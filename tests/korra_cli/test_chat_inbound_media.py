"""K21-369: inbound Telegram media saved by the gateway is downloadable by its owner in the panel."""

import os

import pytest

from gateway.platforms import base as gateway_base
from korra_cli import web_server as server
from tests.korra_cli.test_chat_generated_images import _generated_image, image_chat  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"inbound photo"
KINDS = {
    "documents": ("cache_document_from_bytes", "note.txt", "Привет из Telegram\n".encode()),
    "audio": ("cache_audio_from_bytes", ".ogg", b"OggS" + b"\x00" * 40),
    "videos": ("cache_video_from_bytes", ".mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 40),
}


def _inbound(profile, kind):
    """Store a file exactly as the gateway adapters do, in the profile's own cache."""
    function, argument, raw = KINDS[kind]
    with server._config_profile_scope(profile):
        saved = getattr(gateway_base, function)(raw, argument)
    return gateway_base.Path(saved), raw


def _modes(monkeypatch, mode):
    if mode != "fleet":
        monkeypatch.delenv("KORRA_UI_MODE")
    if mode == "local":
        monkeypatch.delenv("KORRA_DASHBOARD_FILES_ROOT")


@pytest.mark.parametrize("profile", ["default", "designer"])
@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("mode", ["fleet", "forced", "local"])
def test_owner_downloads_inbound_media_of_own_profile(image_chat, monkeypatch, profile, kind, mode):  # noqa: F811
    client, _ = image_chat
    _modes(monkeypatch, mode)
    path, raw = _inbound(profile, kind)
    assert path.parent.name == kind and path.parent.parent.name == "cache"
    for params in ({"path": str(path)}, {"path": str(path), "profile": profile}):
        descriptor = client.get("/api/files/attachment", params=params)
        assert descriptor.status_code == 200, descriptor.text
        assert descriptor.json()["size"] == len(raw)
        response = client.get("/api/files/download", params={**params, "chat": 1})
        assert response.status_code == 200, response.text
        assert response.content == raw
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_foreign_profile_inbound_media_is_denied(image_chat, kind):  # noqa: F811
    client, _ = image_chat
    path, _ = _inbound("other", kind)
    for profile in ("designer", "default"):
        for endpoint in ("/api/files/attachment", "/api/files/download"):
            response = client.get(endpoint, params={"path": str(path), "profile": profile, "chat": 1})
            assert response.status_code == 403, (profile, endpoint, response.text)


@pytest.mark.parametrize("case", [
    "traversal", "symlink", "directory_symlink", "cache_symlink", "env", "auth", "state_db",
    "screenshots", "cache_root", "profile_file", "data_file", "workspace_other_profile", "directory", "fifo",
])
def test_inbound_media_gate_does_not_open_other_data(image_chat, case):  # noqa: F811
    client, data = image_chat
    path, raw = _inbound("designer", "documents")
    home = path.parents[2]
    if case == "traversal":
        path = path.parent / ".." / "documents" / path.name
    elif case == "symlink":
        outside = data / "outside.txt"
        outside.write_bytes(raw)
        path = path.parent / "link.txt"
        path.symlink_to(outside)
    elif case in {"directory_symlink", "cache_symlink"}:
        outside = data / "outside"
        outside.mkdir()
        (outside / "documents").mkdir()
        (outside / "documents" / path.name).write_bytes(raw)
        (outside / path.name).write_bytes(raw)
        path.unlink()
        if case == "directory_symlink":
            path.parent.rmdir()
            path.parent.symlink_to(outside, target_is_directory=True)
        else:
            path.parent.rmdir()
            path.parent.parent.rmdir()
            path.parent.parent.symlink_to(outside, target_is_directory=True)
    elif case in {"env", "auth", "state_db"}:
        path = path.parent / {"env": ".env", "auth": "auth.json", "state_db": "state.db"}[case]
        path.write_bytes(raw)
    elif case == "screenshots":
        path = home / "cache" / "screenshots" / "shot.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    elif case == "cache_root":
        path = home / "cache" / "file.txt"
        path.write_bytes(raw)
    elif case in {"profile_file", "data_file", "workspace_other_profile"}:
        path = {"profile_file": home / "file.txt", "data_file": data / "file.txt",
                "workspace_other_profile": data / "profiles" / "other" / "workspace" / "file.txt"}[case]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    elif case == "directory":
        path = path.parent
    elif case == "fifo":
        path = path.parent / "pipe.txt"
        os.mkfifo(path)
    for endpoint in ("/api/files/attachment", "/api/files/download"):
        response = client.get(endpoint, params={"path": str(path), "profile": "designer", "chat": 1})
        assert response.status_code == 403, (case, endpoint, response.text)


def test_inbound_media_permission_is_limited_to_chat_delivery(image_chat):  # noqa: F811
    client, _ = image_chat
    path, raw = _inbound("designer", "documents")
    params = {"path": str(path), "profile": "designer"}
    for endpoint in ("/api/files", "/api/files/read", "/api/files/download", "/api/files/stream"):
        assert client.get(endpoint, params=params).status_code == 403
    assert client.get("/api/files", params={**params, "path": str(path.parent)}).status_code == 403
    assert client.post("/api/files/trash", params={"profile": "designer"}, json={
        "path": str(path), "expected_revision": "0" * 64,
    }).status_code == 403
    assert client.post("/api/files/upload", params={"profile": "designer"}, json={
        "path": str(path), "data_url": "data:text/plain;base64,eA==",
    }).status_code == 403
    assert path.read_bytes() == raw


def test_inbound_media_requires_auth_and_deleted_file_is_not_found(image_chat):  # noqa: F811
    client, _ = image_chat
    path, _ = _inbound("designer", "documents")
    params = {"path": str(path), "profile": "designer", "chat": 1}
    headers = dict(client.headers)
    client.headers.clear()
    for endpoint in ("/api/files/attachment", "/api/files/download"):
        assert client.get(endpoint, params=params).status_code == 401
    client.headers.update(headers)
    path.unlink()
    for endpoint in ("/api/files/attachment", "/api/files/download"):
        assert client.get(endpoint, params=params).status_code == 404


def test_images_keep_their_signature_check_and_documents_do_not_become_images(image_chat):  # noqa: F811
    client, _ = image_chat
    path, raw = _generated_image("designer")
    fake = path.parent / "photo.png"
    fake.write_bytes(b"not an image")
    for endpoint in ("/api/files/attachment", "/api/files/download"):
        assert client.get(endpoint, params={"path": str(fake), "profile": "designer", "chat": 1}).status_code == 403
    document, _ = _inbound("designer", "documents")
    document.write_bytes(PNG)
    descriptor = client.get("/api/files/attachment", params={"path": str(document), "profile": "designer"})
    assert descriptor.status_code == 200
    assert descriptor.json()["reader"] != "image"
