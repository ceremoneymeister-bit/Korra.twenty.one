"""A19R6-01: profile image output reaches chat without opening profile storage."""

import importlib
import io
import os
from pathlib import Path

import pytest
from PIL import Image
from starlette.requests import Request
from starlette.testclient import TestClient

from korra_cli import web_server as server


@pytest.fixture
def image_chat(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    for name in ("designer", "other"):
        (data / "profiles" / name).mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("KORRA_HOME", str(data))
    monkeypatch.setenv("HERMES_HOME", str(data))
    monkeypatch.setenv("KORRA_UI_MODE", "fleet")
    # Real cabinet layout: inherited DATA root, overridden by the fleet lock.
    monkeypatch.setenv("KORRA_DASHBOARD_FILES_ROOT", str(data))
    monkeypatch.setattr(server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(server.app.state, "bound_host", None, raising=False)
    with TestClient(server.app) as client:
        client.headers[server._SESSION_HEADER_NAME] = server._SESSION_TOKEN
        assert client.get("/api/files").json()["locked_root"] == str(data / "workspace")
        yield client, data


def _generated_image(profile, output_format="png"):
    """Use the generator's actual publication path, without any model call."""
    plugin = importlib.import_module("plugins.image_gen.openai-codex")
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, format=output_format.upper())
    with server._config_profile_scope(profile):
        home, directory = plugin._profile_image_output_dir()
        path = directory / f"openai_codex_fixture.{output_format}"
        plugin._atomic_write_validated_image(
            path, output.getvalue(), output_format, allowed_root=home
        )
    return path, output.getvalue()


@pytest.mark.parametrize("profile", ["default", "designer"])
@pytest.mark.parametrize("output_format", ["png", "jpeg", "webp"])
@pytest.mark.parametrize("mode", ["fleet", "forced", "local"])
def test_generated_image_visible_openable_and_downloadable(image_chat, monkeypatch, profile, output_format, mode):
    from gateway.platforms.base import BasePlatformAdapter

    client, _ = image_chat
    if mode != "fleet":
        monkeypatch.delenv("KORRA_UI_MODE")
    if mode == "local":
        monkeypatch.delenv("KORRA_DASHBOARD_FILES_ROOT")
    path, raw = _generated_image(profile, output_format)
    reference = f'MEDIA:"{path}"'
    media, _ = BasePlatformAdapter.extract_media(reference)
    assert media == [(str(path), False)]
    params = {"profile": profile, "path": reference}
    descriptor = client.get("/api/files/attachment", params=params)
    assert descriptor.status_code == 200, descriptor.text
    assert descriptor.json()["reader"] == "image"
    assert descriptor.json()["size"] == len(raw)
    assert descriptor.json()["path"] == str(path)
    for inline in (0, 1):
        response = client.get(
            "/api/files/download", params={**params, "chat": 1, "inline": inline}
        )
        assert response.status_code == 200, response.text
        assert response.content == raw
        assert response.headers["content-type"] == f"image/{output_format}"
        assert response.headers["content-disposition"].startswith(
            "inline;" if inline else "attachment;"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "private, no-store"
    assert path.read_bytes() == raw  # Telegram still has the original file.


@pytest.mark.parametrize("case", [
    "foreign", "default", "traversal", "symlink", "directory_symlink", "cache_symlink",
    "fake_image", "svg", "env", "auth", "other_cache", "profile_file",
    "data_file", "directory",
])
def test_cache_image_delivery_denies_other_data(image_chat, case):
    client, data = image_chat
    path, raw = _generated_image("designer")
    home = path.parents[2]
    if case == "foreign":
        path, _ = _generated_image("other")
    elif case == "default":
        path, _ = _generated_image("default")
    elif case == "traversal":
        path = path.parent / ".." / "images" / path.name
    elif case == "symlink":
        outside = data / "outside.png"
        outside.write_bytes(raw)
        path = path.parent / "link.png"
        path.symlink_to(outside)
    elif case in {"directory_symlink", "cache_symlink"}:
        outside = data / "outside"
        outside.mkdir()
        (outside / path.name).write_bytes(raw)
        path.unlink()
        path.parent.rmdir()
        if case == "directory_symlink":
            path.parent.symlink_to(outside, target_is_directory=True)
        else:
            (outside / "images").mkdir()
            (outside / path.name).rename(outside / "images" / path.name)
            path.parent.parent.rmdir()
            path.parent.parent.symlink_to(outside, target_is_directory=True)
    elif case in {"fake_image", "svg", "env", "auth"}:
        name = {"fake_image": "fake.png", "svg": "active.svg", "env": ".env", "auth": "auth.json"}[case]
        path = path.parent / name
        path.write_bytes(raw if case in {"env", "auth"} else
                         b"<svg xmlns='http://www.w3.org/2000/svg'/>" if case == "svg" else b"not an image")
    elif case in {"other_cache", "profile_file", "data_file"}:
        path = {"other_cache": home / "cache" / "documents" / "file.png",
                "profile_file": home / "file.png", "data_file": data / "file.png"}[case]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    elif case == "directory":
        path = path.parent
    for endpoint in ("/api/files/attachment", "/api/files/download"):
        response = client.get(endpoint, params={"path": str(path), "profile": "designer", "chat": 1})
        assert response.status_code == 403, (case, endpoint, response.text)


def test_cache_permission_does_not_enable_files_listing_read_or_mutation(image_chat):
    client, _ = image_chat
    path, raw = _generated_image("designer")
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


def test_cache_image_requires_auth_and_is_revalidated(image_chat):
    client, _ = image_chat
    path, _ = _generated_image("designer")
    params = {"path": str(path), "profile": "designer", "chat": 1}
    client.headers.clear()
    for endpoint in ("/api/files/attachment", "/api/files/download"):
        assert client.get(endpoint, params=params).status_code == 401
    client.headers[server._SESSION_HEADER_NAME] = server._SESSION_TOKEN
    assert client.get("/api/files/attachment", params=params).status_code == 200
    path.write_bytes(b"replaced with non-image")
    assert client.get("/api/files/attachment", params=params).status_code == 403
    assert client.get("/api/files/download", params=params).status_code == 403


@pytest.mark.parametrize("profile", [None, "default", "designer"])
@pytest.mark.parametrize("mode", ["fleet", "forced", "local"])
def test_foreign_profile_image_is_denied_even_with_a_permissive_files_root(image_chat, monkeypatch, profile, mode):
    client, _ = image_chat
    path, _ = _generated_image("other")
    if mode != "fleet":
        monkeypatch.delenv("KORRA_UI_MODE")
    if mode == "local":
        monkeypatch.delenv("KORRA_DASHBOARD_FILES_ROOT")
    params = {"path": str(path), "chat": 1}
    if profile:
        params["profile"] = profile
    for endpoint in ("/api/files/attachment", "/api/files/download"):
        assert client.get(endpoint, params=params).status_code == 403


def test_image_signature_controls_preview_kind_and_mime(image_chat):
    client, _ = image_chat
    path, raw = _generated_image("designer")
    renamed = path.with_suffix(".html")
    path.rename(renamed)
    params = {"path": renamed.as_uri(), "profile": "designer", "chat": 1}
    descriptor = client.get("/api/files/attachment", params=params)
    assert descriptor.status_code == 200
    assert descriptor.json()["kind"] == "png"
    response = client.get("/api/files/download", params={**params, "inline": 1})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == raw


def test_generated_image_can_be_reused_in_its_chat_with_a_body_profile(image_chat):
    _, _data = image_chat
    path, _ = _generated_image("designer")
    request = Request({"type": "http", "query_string": b""})
    body = {"messages": [{"role": "user", "content": "Измени"}],
            "attachments": [{"path": str(path)}]}
    server._apply_chat_attachments(body, profile="designer", request=request)
    assert str(path) in body["messages"][0]["content"]


def test_process_profile_is_used_when_chat_has_no_profile_query(image_chat, monkeypatch):
    client, data = image_chat
    path, raw = _generated_image("designer")
    home = data / "profiles" / "designer"
    monkeypatch.setenv("KORRA_HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    response = client.get("/api/files/download", params={"path": str(path), "chat": 1})
    assert response.status_code == 200
    assert response.content == raw


@pytest.mark.linux_only
def test_cache_special_file_is_denied_without_opening_it(image_chat):
    client, _ = image_chat
    path, _ = _generated_image("designer")
    fifo = path.parent / "pipe.png"
    os.mkfifo(fifo)
    for endpoint in ("/api/files/attachment", "/api/files/download"):
        assert client.get(endpoint, params={"path": str(fifo), "profile": "designer", "chat": 1}).status_code == 403
