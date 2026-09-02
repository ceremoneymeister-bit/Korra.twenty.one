"""Tests for the dashboard-managed file browser API."""

from types import SimpleNamespace
import zipfile

import pytest
from starlette.testclient import TestClient

from hermes_cli import web_server


def _client_with_app_state():
    prev_auth_required = getattr(web_server.app.state, "auth_required", None)
    prev_bound_host = getattr(web_server.app.state, "bound_host", None)
    web_server.app.state.auth_required = False
    web_server.app.state.bound_host = None

    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    return client, prev_auth_required, prev_bound_host


def _restore_app_state(prev_auth_required, prev_bound_host):
    if prev_auth_required is None:
        delattr(web_server.app.state, "auth_required")
    else:
        web_server.app.state.auth_required = prev_auth_required
    if prev_bound_host is None:
        if hasattr(web_server.app.state, "bound_host"):
            delattr(web_server.app.state, "bound_host")
    else:
        web_server.app.state.bound_host = prev_bound_host


def _close_client(client):
    close = getattr(client, "close", None)
    if close is not None:
        close()


@pytest.fixture
def forced_files_client(monkeypatch, tmp_path):
    root = tmp_path / "data"
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(root))

    client, prev_auth_required, prev_bound_host = _client_with_app_state()
    try:
        yield client, root
    finally:
        _close_client(client)
        _restore_app_state(prev_auth_required, prev_bound_host)


@pytest.fixture
def local_files_client(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("HERMES_DASHBOARD_FILES_ROOT", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))

    client, prev_auth_required, prev_bound_host = _client_with_app_state()
    try:
        yield client, home
    finally:
        _close_client(client)
        _restore_app_state(prev_auth_required, prev_bound_host)














def _seed_file(client, root, name="out/hello.txt"):
    file_path = root / name
    created = client.post(
        "/api/files/upload",
        json={"path": str(file_path), "data_url": "data:text/plain;base64,aGVsbG8="},
    )
    assert created.status_code == 200
    return file_path




def test_download_authenticates_via_query_token(forced_files_client):
    client, root = forced_files_client
    file_path = _seed_file(client, root, name="out/demo.mp4")
    active_content = _seed_file(client, root, name="out/page.html")

    # Drop the session header so only the ?token= query param authenticates —
    # mirrors a browser/shell-opened download that can't set the session header.
    del client.headers[web_server._SESSION_HEADER_NAME]

    ok = client.get(
        "/api/files/download",
        params={"path": str(file_path), "token": web_server._SESSION_TOKEN},
    )
    assert ok.status_code == 200
    assert ok.content == b"hello"
    assert ok.headers["content-disposition"].startswith("attachment;")

    playback = client.get(
        "/api/files/download",
        params={"path": str(file_path), "token": web_server._SESSION_TOKEN},
        headers={"Sec-Fetch-Dest": "video", "Range": "bytes=1-3"},
    )
    assert playback.status_code == 206
    assert playback.content == b"ell"
    assert playback.headers["content-disposition"].startswith("inline;")
    assert playback.headers["x-content-type-options"] == "nosniff"

    rejected = client.get(
        "/api/files/download",
        params={"path": str(active_content), "token": web_server._SESSION_TOKEN},
        headers={"Sec-Fetch-Dest": "video"},
    )
    assert rejected.status_code == 415

    assert client.get(
        "/api/files/download", params={"path": str(file_path), "token": "nope"}
    ).status_code == 401
    assert client.get(
        "/api/files/download", params={"path": str(file_path)}
    ).status_code == 401


def test_stream_requires_header_auth_and_supports_ranges(forced_files_client):
    client, root = forced_files_client
    file_path = _seed_file(client, root, name="out/demo.mp4")

    # Electron's main-process proxy supplies the connection credential as a
    # header. Unlike browser-visible download links, the stream endpoint must
    # not accept credentials in its URL.
    params = {"path": str(file_path)}

    full = client.get("/api/files/stream", params=params)
    assert full.status_code == 200
    assert full.content == b"hello"
    assert full.headers["content-type"] == "video/mp4"
    assert full.headers["content-disposition"].startswith("inline;")
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["x-content-type-options"] == "nosniff"

    partial = client.get(
        "/api/files/stream",
        params=params,
        headers={"Range": "bytes=1-3"},
    )
    assert partial.status_code == 206
    assert partial.content == b"ell"
    assert partial.headers["content-range"] == "bytes 1-3/5"
    assert partial.headers["content-disposition"].startswith("inline;")
    assert partial.headers["x-content-type-options"] == "nosniff"

    head = client.head("/api/files/stream", params=params)
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == "5"
    assert head.headers["x-content-type-options"] == "nosniff"

    del client.headers[web_server._SESSION_HEADER_NAME]
    assert client.get(
        "/api/files/stream",
        params={"path": str(file_path), "token": web_server._SESSION_TOKEN},
    ).status_code == 401
    assert client.get("/api/files/stream", params=params).status_code == 401


def test_stream_rejects_non_media_active_content(forced_files_client):
    client, root = forced_files_client

    for name in ("out/page.html", "out/image.svg"):
        file_path = _seed_file(client, root, name=name)
        response = client.get("/api/files/stream", params={"path": str(file_path)})
        assert response.status_code == 415
        assert response.json()["detail"] == "Unsupported media type"


def test_query_token_does_not_authenticate_other_endpoints(forced_files_client):
    client, root = forced_files_client
    file_path = _seed_file(client, root)

    del client.headers[web_server._SESSION_HEADER_NAME]

    # The query-token escape hatch is scoped to downloads only; it must not
    # unlock the rest of the API surface.
    leaked = client.get(
        "/api/files/read",
        params={"path": str(file_path), "token": web_server._SESSION_TOKEN},
    )
    assert leaked.status_code == 401




# ---------------------------------------------------------------------------
# Streaming multipart upload (/api/files/upload-stream) — NS-501
# ---------------------------------------------------------------------------








def test_stream_upload_cleans_temp_on_cancellation(forced_files_client):
    """A client disconnect mid-stream (asyncio.CancelledError) must not leak a temp file.

    CancelledError is a BaseException, not an Exception, so it bypasses the
    endpoint's ``except`` clauses entirely. The cleanup therefore lives in a
    ``finally`` keyed on a success flag — without it, every aborted large
    upload (the exact NS-501 scenario) would orphan a partial ``.upload`` temp
    file in the target directory. We invoke the endpoint coroutine directly so
    the BaseException propagates instead of being swallowed by the test client.
    """
    import asyncio

    _client, root = forced_files_client
    target = root / "out" / "aborted.bin"
    target.parent.mkdir(parents=True, exist_ok=True)

    class _AbortingUpload:
        """UploadFile stand-in that yields one chunk then aborts like a dropped client."""

        filename = "aborted.bin"

        def __init__(self):
            self._calls = 0

        async def read(self, _size):
            self._calls += 1
            if self._calls == 1:
                return b"partial chunk before the client vanished"
            raise asyncio.CancelledError()

        async def close(self):
            return None

    request = SimpleNamespace()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            web_server.upload_managed_file_stream(
                request=request,
                file=_AbortingUpload(),
                path=str(target),
                overwrite=True,
            )
        )

    # No partial data was promoted into place ...
    assert not target.exists()
    # ... and no .upload temp file was left behind.
    leftovers = [p.name for p in target.parent.iterdir() if ".upload" in p.name]
    assert leftovers == [], f"temp upload files leaked on cancellation: {leftovers}"


def test_stream_upload_no_overwrite_preserves_existing_file(forced_files_client):
    client, root = forced_files_client
    target = root / "out" / "report.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("первая версия", encoding="utf-8")

    response = client.post(
        "/api/files/upload-stream",
        data={"path": str(target), "overwrite": "false"},
        files={"file": ("report.txt", "вторая версия", "text/plain")},
    )

    assert response.status_code == 409
    assert target.read_text(encoding="utf-8") == "первая версия"
    assert not any(".upload" in item.name for item in target.parent.iterdir())


def test_stream_upload_overwrite_requires_current_revision(forced_files_client):
    client, root = forced_files_client
    target = root / "out" / "report.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("первая версия", encoding="utf-8")
    entry = client.get("/api/files", params={"path": str(target.parent)}).json()["entries"][0]

    missing_revision = client.post(
        "/api/files/upload-stream",
        data={"path": str(target), "overwrite": "true"},
        files={"file": ("report.txt", "вторая версия", "text/plain")},
    )
    assert missing_revision.status_code == 409
    assert target.read_text(encoding="utf-8") == "первая версия"

    target.write_text("чужая свежая правка", encoding="utf-8")
    stale_revision = client.post(
        "/api/files/upload-stream",
        data={
            "path": str(target),
            "overwrite": "true",
            "expected_revision": entry["revision"],
        },
        files={"file": ("report.txt", "потерянная версия", "text/plain")},
    )
    assert stale_revision.status_code == 409
    assert target.read_text(encoding="utf-8") == "чужая свежая правка"

    current = client.get("/api/files", params={"path": str(target.parent)}).json()["entries"][0]
    replaced = client.post(
        "/api/files/upload-stream",
        data={
            "path": str(target),
            "overwrite": "true",
            "expected_revision": current["revision"],
        },
        files={"file": ("report.txt", "согласованная версия", "text/plain")},
    )
    assert replaced.status_code == 200
    assert target.read_text(encoding="utf-8") == "согласованная версия"


def test_stream_upload_does_not_resurrect_target_removed_before_replace(
    forced_files_client,
    monkeypatch,
):
    client, root = forced_files_client
    target = root / "out" / "report.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("исходная версия", encoding="utf-8")
    revision = client.get("/api/files", params={"path": str(target.parent)}).json()[
        "entries"
    ][0]["revision"]
    original_validate = web_server._validate_managed_upload_destination

    def _remove_then_validate(path, **kwargs):
        path.unlink()
        return original_validate(path, **kwargs)

    monkeypatch.setattr(
        web_server,
        "_validate_managed_upload_destination",
        _remove_then_validate,
    )
    response = client.post(
        "/api/files/upload-stream",
        data={
            "path": str(target),
            "overwrite": "true",
            "expected_revision": revision,
        },
        files={"file": ("report.txt", "не должна воскреснуть", "text/plain")},
    )

    assert response.status_code == 409
    assert not target.exists()
    assert not any(".upload" in item.name for item in target.parent.iterdir())


def test_sensitive_env_files_hidden_from_listing(forced_files_client):
    """Regression test for #57505: .env files must not appear in directory listings."""
    client, root = forced_files_client

    # Create a regular file and .env variants including shorthand suffixes.
    root.mkdir(parents=True, exist_ok=True)
    regular = root / "config.txt"
    regular.write_text("safe content")
    env_file = root / ".env"
    env_file.write_text("SECRET_KEY=abc123")
    env_local = root / ".env.local"
    env_local.write_text("LOCAL_SECRET=def456")
    env_prod = root / ".env.prod"
    env_prod.write_text("PROD_SECRET=ghi789")

    listing = client.get("/api/files", params={"path": str(root)})
    assert listing.status_code == 200
    names = [e["name"] for e in listing.json()["entries"]]
    assert "config.txt" in names
    assert ".env" not in names
    assert ".env.local" not in names
    assert ".env.prod" not in names












def test_other_credential_store_basenames_blocked(forced_files_client):
    """Regression: the managed-files guard must cover the same credential
    basenames as gateway.platforms.base._ROOT_CREDENTIAL_FILES and
    agent.file_safety.get_read_block_error, not just .env — an operator can
    point the managed root at HERMES_HOME itself (#57505), which contains
    all of these live secret stores."""
    client, root = forced_files_client
    root.mkdir(parents=True, exist_ok=True)

    for name in (
        "auth.json",
        "auth.lock",
        "credentials",
        "config.yaml",
        ".anthropic_oauth.json",
        "google_token.json",
        "google_oauth_pending.json",
        "google_oauth.json",
        "webhook_subscriptions.json",
        "bws_cache.json",
        "bws_cache.enc.json",
    ):
        p = root / name
        p.write_text("SECRET=abc123")
        assert client.get("/api/files/read", params={"path": str(p)}).status_code == 403, name
        assert client.get("/api/files/download", params={"path": str(p)}).status_code == 403, name
        assert client.get("/api/files/stream", params={"path": str(p)}).status_code == 403, name

    listing = client.get("/api/files", params={"path": str(root)})
    names = [e["name"] for e in listing.json()["entries"]]
    assert names == []




def test_credential_dir_trees_blocked_on_subdir_descent(forced_files_client):
    """Regression: mcp-tokens/ (live MCP OAuth tokens) and pairing/ are denied
    as whole directory trees by both canonical guards
    (gateway.platforms.base._ROOT_CREDENTIAL_DIRS and
    agent.file_safety). A basename-only check would still expose their
    per-server files (e.g. ``mcp-tokens/github.json``) once the browser
    descends into the subdir. The managed-files guard must block any path with
    a credential-directory component, not just leaf basenames."""
    client, root = forced_files_client
    root.mkdir(parents=True, exist_ok=True)

    # A per-server MCP token file with a NON-canonical basename that the
    # basename denylist alone would not catch.
    mcp_dir = root / "mcp-tokens"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    mcp_file = mcp_dir / "github.json"
    mcp_file.write_text('{"access_token": "SECRET"}\n')

    pairing_dir = root / "pairing"
    pairing_dir.mkdir(parents=True, exist_ok=True)
    pairing_file = pairing_dir / "device-abc"
    pairing_file.write_text("PAIRING-SECRET\n")

    # The token dirs themselves must not appear in the root listing.
    root_names = [e["name"] for e in client.get(
        "/api/files", params={"path": str(root)}).json()["entries"]]
    assert "mcp-tokens" not in root_names
    assert "pairing" not in root_names

    # Read/download of the per-server files must be denied even though their
    # basenames aren't in _SENSITIVE_MANAGED_FILE_BASENAMES.
    for p in (mcp_file, pairing_file):
        assert client.get("/api/files/read", params={"path": str(p)}).status_code == 403, str(p)
        assert client.get("/api/files/download", params={"path": str(p)}).status_code == 403, str(p)
        assert client.get("/api/files/stream", params={"path": str(p)}).status_code == 403, str(p)

    # Direct descent into a credential directory is rejected as well; returning
    # an empty listing would still confirm that the private path exists.
    mcp_listing = client.get("/api/files", params={"path": str(mcp_dir)})
    assert mcp_listing.status_code == 403


def test_fleet_files_are_confined_to_a_dedicated_workspace(monkeypatch, tmp_path):
    """Fleet users must never land in the credential-bearing HERMES_HOME root."""
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    (hermes_home / ".secrets").mkdir()
    (hermes_home / ".secrets" / "provider-token").write_text("secret")
    monkeypatch.delenv("HERMES_DASHBOARD_FILES_ROOT", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("KORRA_UI_MODE", "fleet")

    client, prev_auth_required, prev_bound_host = _client_with_app_state()
    try:
        response = client.get("/api/files")
        assert response.status_code == 200
        payload = response.json()
        workspace = hermes_home / "workspace"
        assert payload["path"] == str(workspace)
        assert payload["locked_root"] == str(workspace)
        assert payload["can_change_path"] is False
        assert payload["entries"] == []
        assert workspace.is_dir()

        escaped = client.get("/api/files", params={"path": str(hermes_home)})
        assert escaped.status_code == 403
    finally:
        _close_client(client)
        _restore_app_state(prev_auth_required, prev_bound_host)


def test_managed_listing_returns_more_than_one_thousand_entries(forced_files_client):
    client, root = forced_files_client
    root.mkdir(parents=True, exist_ok=True)
    for index in range(1_005):
        (root / f"файл-{index:04d}.txt").touch()

    response = client.get("/api/files", params={"path": str(root)})

    assert response.status_code == 200
    assert len(response.json()["entries"]) == 1_005


def test_managed_listing_hides_symlinks_and_direct_access_rejects_them(
    forced_files_client,
):
    client, root = forced_files_client
    root.mkdir(parents=True, exist_ok=True)
    outside = root.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    regular = root / "regular.txt"
    regular.write_text("safe", encoding="utf-8")
    (root / "outside-link.txt").symlink_to(outside)
    (root / "inside-link.txt").symlink_to(regular)
    (root / "broken-link.txt").symlink_to(root / "missing.txt")

    listing = client.get("/api/files", params={"path": str(root)})
    assert listing.status_code == 200
    assert [entry["name"] for entry in listing.json()["entries"]] == ["regular.txt"]

    for link in (root / "outside-link.txt", root / "inside-link.txt", root / "broken-link.txt"):
        assert client.get("/api/files/read", params={"path": str(link)}).status_code == 403
        assert client.post(
            "/api/files/rename",
            json={
                "path": str(link),
                "new_name": "renamed.txt",
                "expected_revision": "0" * 64,
            },
        ).status_code == 403


def test_unlocked_listing_only_advertises_mutations_inside_default_home(local_files_client):
    client, home = local_files_client
    outside = home.parent / "shared"
    outside.mkdir()
    (outside / "read-only.txt").write_text("visible", encoding="utf-8")

    response = client.get("/api/files", params={"path": str(outside)})

    assert response.status_code == 200
    assert response.json()["entries"][0]["capabilities"] == {
        "rename": False,
        "trash": False,
    }


def test_managed_text_preview_and_versioned_save(forced_files_client):
    client, root = forced_files_client
    target = root / "notes.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Первая версия\n", encoding="utf-8")

    preview = client.get("/api/files/text", params={"path": str(target)})
    assert preview.status_code == 200
    opened = preview.json()
    assert opened["text"] == "# Первая версия\n"
    assert opened["binary"] is False
    assert opened["editable"] is True
    assert len(opened["sha256"]) == 64

    saved = client.put(
        "/api/files/text",
        json={
            "path": str(target),
            "content": "# Вторая версия\n",
            "expected_sha256": opened["sha256"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["text"] == "# Вторая версия\n"
    assert target.read_text(encoding="utf-8") == "# Вторая версия\n"

    stale = client.put(
        "/api/files/text",
        json={
            "path": str(target),
            "content": "потерянная правка",
            "expected_sha256": opened["sha256"],
        },
    )
    assert stale.status_code == 409
    assert target.read_text(encoding="utf-8") == "# Вторая версия\n"


def test_managed_office_preview_reads_real_xlsx(forced_files_client):
    client, root = forced_files_client
    target = root / "смета.xlsx"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Смета" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row><c t="inlineStr"><is><t>Позиция</t></is></c>'
            '<c t="inlineStr"><is><t>Цена</t></is></c></row>'
            '<row><c t="inlineStr"><is><t>Монтаж</t></is></c>'
            '<c t="n"><v>1000</v></c></row>'
            '</sheetData></worksheet>',
        )

    response = client.get("/api/files/office", params={"path": str(target)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "xlsx"
    assert payload["blocks"][0] == {"type": "heading", "text": "Лист: Смета"}
    assert payload["blocks"][1]["rows"][0] == ["Позиция", "Цена"]


def test_managed_office_preview_reads_real_docx(forced_files_client):
    client, root = forced_files_client
    target = root / "описание.docx"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Описание проекта</w:t></w:r></w:p></w:body>'
            '</w:document>',
        )

    response = client.get("/api/files/office", params={"path": str(target)})

    assert response.status_code == 200
    assert response.json()["kind"] == "docx"
    assert response.json()["blocks"] == [
        {"type": "paragraph", "text": "Описание проекта"},
    ]


def test_managed_office_preview_reads_real_pptx(forced_files_client):
    client, root = forced_files_client
    target = root / "презентация.pptx"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<p:cSld><p:spTree><p:sp><p:txBody>'
            '<a:p><a:r><a:t>Заголовок</a:t></a:r></a:p>'
            '<a:p><a:r><a:t>Текст слайда</a:t></a:r></a:p>'
            '</p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
        )

    response = client.get("/api/files/office", params={"path": str(target)})

    assert response.status_code == 200
    assert response.json()["kind"] == "pptx"
    assert response.json()["blocks"] == [
        {"type": "heading", "text": "Слайд 1: Заголовок"},
        {"type": "paragraph", "text": "Текст слайда"},
    ]


def test_managed_office_preview_rejects_excessive_unpacked_size(
    forced_files_client,
    monkeypatch,
):
    client, root = forced_files_client
    target = root / "архив.docx"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "x" * 32)
    monkeypatch.setattr(web_server, "_OFFICE_MAX_UNCOMPRESSED_BYTES", 16)

    response = client.get("/api/files/office", params={"path": str(target)})

    assert response.status_code == 413
    assert "распаковки" in response.json()["detail"]


def test_managed_office_preview_rejects_when_worker_slots_are_busy(forced_files_client):
    client, root = forced_files_client
    target = root / "описание.docx"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Текст</w:t></w:r></w:p></w:body></w:document>',
        )

    assert web_server._OFFICE_PREVIEW_SLOTS.acquire(blocking=False)
    assert web_server._OFFICE_PREVIEW_SLOTS.acquire(blocking=False)
    try:
        response = client.get("/api/files/office", params={"path": str(target)})
    finally:
        web_server._OFFICE_PREVIEW_SLOTS.release()
        web_server._OFFICE_PREVIEW_SLOTS.release()

    assert response.status_code == 429


def test_managed_office_table_preview_limits_columns():
    blocks, truncated = web_server._office_blocks_from_text(
        "\t".join(f"ячейка-{index}" for index in range(100))
    )

    assert truncated is True
    assert blocks[0]["type"] == "table"
    assert len(blocks[0]["rows"][0]) == web_server._OFFICE_MAX_TABLE_COLS


def test_inline_preview_is_nosniff_and_sandboxed(forced_files_client):
    client, root = forced_files_client
    target = root / "vector.svg"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        encoding="utf-8",
    )

    response = client.get(
        "/api/files/download",
        params={"path": str(target), "inline": "1"},
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert response.headers["cache-control"] == "private, no-store"


def test_managed_entry_can_be_renamed_with_optimistic_revision(forced_files_client):
    client, root = forced_files_client
    source = root / "черновик.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("версия 1", encoding="utf-8")
    listing = client.get("/api/files", params={"path": str(root)}).json()
    entry = next(item for item in listing["entries"] if item["name"] == source.name)
    assert entry["capabilities"] == {"rename": True, "trash": True}

    renamed = client.post(
        "/api/files/rename",
        json={
            "path": str(source),
            "new_name": "готово.txt",
            "expected_revision": entry["revision"],
        },
    )
    assert renamed.status_code == 200
    assert not source.exists()
    assert (root / "готово.txt").read_text(encoding="utf-8") == "версия 1"

    stale = client.post(
        "/api/files/rename",
        json={
            "path": str(root / "готово.txt"),
            "new_name": "ещё-раз.txt",
            "expected_revision": entry["revision"],
        },
    )
    assert stale.status_code == 409


def test_managed_trash_round_trip_preserves_relative_directory(forced_files_client):
    client, root = forced_files_client
    source = root / "project" / "notes.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("важно", encoding="utf-8")
    listing = client.get("/api/files", params={"path": str(source.parent)}).json()
    entry = next(item for item in listing["entries"] if item["name"] == source.name)

    trashed = client.post(
        "/api/files/trash",
        json={"path": str(source), "expected_revision": entry["revision"]},
    )
    assert trashed.status_code == 200
    assert not source.exists()
    trash_id = trashed.json()["trash_id"]

    trash = client.get("/api/files/trash")
    assert trash.status_code == 200
    assert trash.json()["entries"][0]["name"] == "notes.txt"
    assert trash.json()["entries"][0]["original_path"] == "project/notes.txt"
    trash_root = root / ".trash"
    stored = next(path for path in trash_root.iterdir() if path.name.startswith(f"{trash_id}__"))
    assert client.get("/api/files", params={"path": str(trash_root)}).status_code == 403
    assert client.get("/api/files/download", params={"path": str(stored)}).status_code == 403

    restored = client.post("/api/files/trash/restore", json={"trash_id": trash_id})
    assert restored.status_code == 200
    assert source.read_text(encoding="utf-8") == "важно"
    assert client.get("/api/files/trash").json()["entries"] == []


def test_managed_trash_rejects_symlink_without_touching_target(forced_files_client):
    client, root = forced_files_client
    root.mkdir(parents=True, exist_ok=True)
    outside = root.parent / "outside-trash"
    outside.mkdir(mode=0o755)
    (root / ".trash").symlink_to(outside, target_is_directory=True)

    response = client.get("/api/files/trash")

    assert response.status_code == 409
    assert outside.stat().st_mode & 0o777 == 0o755
    assert list(outside.iterdir()) == []


def test_managed_file_mutations_reject_private_paths(forced_files_client):
    client, root = forced_files_client
    root.mkdir(parents=True, exist_ok=True)
    secret = root / ".env"
    secret.write_text("TOKEN=secret", encoding="utf-8")
    client.get("/api/files/trash")

    upload_secret = client.post(
        "/api/files/upload-stream",
        data={"path": str(secret), "overwrite": "true"},
        files={"file": (".env", "replacement", "text/plain")},
    )
    upload_internal = client.post(
        "/api/files/upload-stream",
        data={"path": str(root / ".trash" / "forged.txt"), "overwrite": "false"},
        files={"file": ("forged.txt", "forged", "text/plain")},
    )
    delete_secret = client.request(
        "DELETE",
        "/api/files",
        json={"path": str(secret), "recursive": False},
    )

    assert upload_secret.status_code == 403
    assert upload_internal.status_code == 403
    assert delete_secret.status_code == 405
    assert secret.read_text(encoding="utf-8") == "TOKEN=secret"


def test_managed_trash_purge_removes_directory_tree(forced_files_client):
    client, root = forced_files_client
    source = root / "old-project"
    source.mkdir(parents=True)
    (source / "nested.txt").write_text("remove", encoding="utf-8")
    entry = client.get("/api/files", params={"path": str(root)}).json()["entries"][0]

    trashed = client.post(
        "/api/files/trash",
        json={"path": str(source), "expected_revision": entry["revision"]},
    )
    assert trashed.status_code == 200
    purged = client.post(
        "/api/files/trash/purge",
        json={"trash_id": trashed.json()["trash_id"]},
    )
    assert purged.status_code == 200
    assert not source.exists()
    assert client.get("/api/files/trash").json()["entries"] == []


def test_managed_trash_listing_is_paginated(forced_files_client):
    client, root = forced_files_client
    root.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        source = root / f"file-{index}.txt"
        source.write_text(str(index), encoding="utf-8")
        entry = next(
            item
            for item in client.get("/api/files", params={"path": str(root)}).json()["entries"]
            if item["name"] == source.name
        )
        response = client.post(
            "/api/files/trash",
            json={"path": str(source), "expected_revision": entry["revision"]},
        )
        assert response.status_code == 200

    first = client.get("/api/files/trash", params={"offset": 0, "limit": 2}).json()
    second = client.get("/api/files/trash", params={"offset": 2, "limit": 2}).json()

    assert first["total"] == 3
    assert first["has_more"] is True
    assert len(first["entries"]) == 2
    assert second["has_more"] is False
    assert len(second["entries"]) == 1


def test_managed_trash_rejects_forged_restore_path(forced_files_client):
    client, root = forced_files_client
    root.mkdir(parents=True, exist_ok=True)
    trash = root / ".trash"
    trash.mkdir()
    trash_id = "20260902T120000Z-0123456789"
    (trash / f"{trash_id}__notes.txt").write_text("важно", encoding="utf-8")
    (trash / f"{trash_id}.meta.json").write_text(
        '{"name":"notes.txt","original_path":"../outside.txt"}',
        encoding="utf-8",
    )

    response = client.post("/api/files/trash/restore", json={"trash_id": trash_id})

    assert response.status_code == 422
    assert not (root.parent / "outside.txt").exists()


def test_file_manager_frontend_contract_is_present_in_openapi():
    schema = web_server.app.openapi()
    expected = {
        ("/api/files", "get"),
        ("/api/files/text", "get"),
        ("/api/files/text", "put"),
        ("/api/files/office", "get"),
        ("/api/files/download", "get"),
        ("/api/files/upload-stream", "post"),
        ("/api/files/mkdir", "post"),
        ("/api/files/rename", "post"),
        ("/api/files/trash", "get"),
        ("/api/files/trash", "post"),
        ("/api/files/trash/restore", "post"),
        ("/api/files/trash/purge", "post"),
    }

    missing = sorted(
        f"{method.upper()} {path}"
        for path, method in expected
        if method not in schema.get("paths", {}).get(path, {})
    )
    assert missing == []
    assert "delete" not in schema.get("paths", {}).get("/api/files", {})
