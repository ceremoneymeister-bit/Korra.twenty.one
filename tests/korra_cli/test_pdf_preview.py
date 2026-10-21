"""PDF pagination through the real managed-files API and installed Poppler."""
import base64
import hashlib
import shutil
import struct
import subprocess

import pytest

from tests.korra_cli.test_web_server_files import forced_files_client  # noqa: F401


def make_pdf(path):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 2 /Kids [3 0 R 5 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Contents 4 0 R >>",
        b"<< /Length 26 >>\nstream\n1 0 0 rg 0 0 300 400 re f\nendstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Contents 6 0 R >>",
        b"<< /Length 26 >>\nstream\n0 0 1 rg 0 0 300 400 re f\nendstream",
    ]
    content = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(content)
    content += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    content += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    content += f"trailer\n<< /Root 1 0 R /Size {len(offsets)} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.mark.skipif(not (shutil.which("pdfinfo") and shutil.which("pdftoppm")), reason="Poppler is optional outside the managed image")
def test_real_pages_are_readable_images_and_original_is_preserved(forced_files_client):
    client, root = forced_files_client
    pdf = make_pdf(root / "two.pdf")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    images = []
    for page in (1, 2):
        result = client.get("/api/files/read", params={"path": str(pdf), "pdf_page": page, "expected_sha256": digest})
        assert result.status_code == 200, result.text
        body = result.json()
        assert (body["page"], body["pages"]) == (page, 2)
        png = base64.b64decode(body["data_url"].split(",", 1)[1])
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", png[16:24])
        assert 300 <= width <= 1800 and 400 <= height <= 1800
        assert result.headers["cache-control"] == "private, no-store"
        images.append(png)
    assert images[0] != images[1]
    original = client.get("/api/files/download", params={"path": str(pdf)})
    assert original.content == pdf.read_bytes()
    ordinary_read = client.get("/api/files/read", params={"path": str(pdf)}).json()
    assert base64.b64decode(ordinary_read["data_url"].split(",", 1)[1]) == pdf.read_bytes()
    assert client.get("/api/files/read", params={"path": str(pdf), "pdf_page": 3}).status_code == 400
    assert client.get("/api/files/read", params={"path": str(pdf), "pdf_page": 0}).status_code == 400


def test_pdf_preview_keeps_file_access_and_revision_checks(forced_files_client):
    client, root = forced_files_client
    pdf = make_pdf(root / "two.pdf")
    assert client.get("/api/files/read", params={"path": str(pdf), "pdf_page": 1, "expected_sha256": "0" * 64}).status_code == 409
    outside = make_pdf(root.parent / "outside.pdf")
    assert client.get("/api/files/read", params={"path": str(outside), "pdf_page": 1}).status_code == 403
    secret = root / ".env"
    secret.write_text("secret")
    assert client.get("/api/files/read", params={"path": str(secret), "pdf_page": 1}).status_code == 403
    assert client.get("/api/files/read", params={"path": str(root / "missing.pdf"), "pdf_page": 1}).status_code == 404


def test_pdf_unavailable_has_actionable_error(forced_files_client, monkeypatch):
    client, root = forced_files_client
    pdf = make_pdf(root / "two.pdf")
    monkeypatch.setenv("PATH", "")
    result = client.get("/api/files/read", params={"path": str(pdf), "pdf_page": 1})
    assert result.status_code == 503
    assert "Скачайте оригинал" in result.json()["detail"]


def test_pdf_timeout_releases_capacity_and_has_actionable_error(forced_files_client, monkeypatch):
    from korra_cli import pdf_preview

    client, root = forced_files_client
    pdf = make_pdf(root / "two.pdf")
    monkeypatch.setattr(pdf_preview.shutil, "which", lambda name: name)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("pdfinfo", 15)

    monkeypatch.setattr(pdf_preview.subprocess, "run", timeout)
    for _ in range(3):
        result = client.get("/api/files/read", params={"path": str(pdf), "pdf_page": 1})
        assert result.status_code == 422  # Not 429 from leaked renderer slots.
        assert "Скачайте оригинал" in result.json()["detail"]


def test_oversized_pdf_is_rejected_before_rendering(forced_files_client):
    client, root = forced_files_client
    root.mkdir(parents=True, exist_ok=True)
    pdf = root / "large.pdf"
    with pdf.open("wb") as stream:
        stream.truncate(51 * 1024 * 1024)
    result = client.get("/api/files/read", params={"path": str(pdf), "pdf_page": 1})
    assert result.status_code == 413
    assert "Скачайте оригинал" in result.json()["detail"]
