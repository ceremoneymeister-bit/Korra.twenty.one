"""Real native-library/CLI checks with synthetic PDFs; no customer document fixtures."""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import shutil
import time

import pytest

# Optional standalone runtime: the CORE-only suite need not install it.
pytest.importorskip("pypdf")
pytest.importorskip("pypdfium2")
pytest.importorskip("pdf_inspector")
pytest.importorskip("PIL")

from PIL import Image, ImageChops
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "calculator/documents/cli.py"


@pytest.fixture(autouse=True)
def isolated_agent_home(tmp_path, monkeypatch):
    # This suite runs through the hermetic runner with --confcutdir to avoid
    # pulling unrelated CORE imports into the standalone document virtualenv.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "agent-home"))


def make_pdf(path: Path, rotations=(0, 0, 0), cropbox=None) -> bytes:
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for index, rotation in enumerate(rotations):
        page = writer.add_blank_page(width=240, height=120)
        if index != 1:
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
                DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 10 Tf 10 60 Td (" + b"Existing OCR numeric 032 " * 12 + b") Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
        if cropbox:
            page.cropbox.lower_left = cropbox[:2]
            page.cropbox.upper_right = cropbox[2:]
        page.rotate(rotation)
    output = BytesIO()
    writer.write(output)
    path.write_bytes(output.getvalue())
    return output.getvalue()


def invoke(tmp_path: Path, command="inspect", *, data=None, sha=None, extra=()):
    source = tmp_path / "source.pdf"
    if data is None:
        data = make_pdf(source)
    else:
        source.write_bytes(data)
    output = tmp_path / "out"
    digest = sha or hashlib.sha256(data).hexdigest()
    result = subprocess.run([sys.executable, str(CLI), command, str(source), "--sha256", digest,
                             "--out", str(output), *extra], capture_output=True, text=True,
                            timeout=30, check=False)
    artifact_path = output / f"{command}.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8")) if artifact_path.exists() else None
    return result, artifact, output


def test_inventory_is_complete_when_only_one_text_page_selected(tmp_path):
    result, artifact, _ = invoke(tmp_path, extra=("--pages", "3", "--max-text-chars", "35"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert artifact["inventory_complete"] is True
    assert [p["page"] for p in artifact["inventory"]] == [1, 2, 3]
    assert artifact["inventory"][1]["native_character_count"] == 0
    assert artifact["inventory"][1]["route"] == "render_and_ocr_or_vision"
    assert [p["page"] for p in artifact["text_pages"]] == [3]
    assert artifact["text_pages"][0]["text_truncated"] is True
    assert len(artifact["text_pages"][0]["markdown"]) <= 35
    assert artifact["source"]["sha256_verified"] is True


def test_existing_ocr_never_becomes_verified_numeric_fact(tmp_path):
    result, artifact, _ = invoke(tmp_path, extra=("--pages", "1,2"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "032" in artifact["text_pages"][0]["markdown"]
    assert artifact["text_pages"][1]["page"] == 2
    assert artifact["text_pages"][1]["parser_suggests_ocr"] is True
    assert artifact["text_pages"][1]["markdown"] == ""
    assert artifact["verification"] == {
        "numeric_facts": "unverified", "numeric_confidence": None, "use_for_calculation": False}
    assert "source_detection_confidence" in artifact["source_detection"]
    assert all(p["numeric_confidence"] is None for p in artifact["inventory"])


def test_sha_mismatch_rejected_before_native_parse(tmp_path):
    result, artifact, output = invoke(tmp_path, data=b"not even a PDF", sha="0" * 64)
    assert result.returncode != 0
    assert artifact["errors"][0]["code"] == "source_sha256_mismatch"
    assert artifact["source"]["sha256_verified"] is False
    assert "parser_versions" not in artifact
    assert not list(output.glob("*.png"))


@pytest.mark.parametrize("pages", ["0", "4", "3-2", "1;2"])
def test_selected_page_bounds_and_syntax(tmp_path, pages):
    result, artifact, _ = invoke(tmp_path, extra=("--pages", pages))
    assert result.returncode != 0
    assert artifact["complete"] is False
    assert artifact["errors"][0]["code"] in {"page_out_of_bounds", "invalid_pages"}


def test_page_limit_preserves_total_and_incomplete_inventory(tmp_path):
    result, artifact, _ = invoke(tmp_path, extra=("--max-pages", "2"))
    assert result.returncode != 0
    assert artifact["page_count"] == 3
    assert artifact["uninspected_page_count"] == 3
    assert artifact["inventory_complete"] is False
    assert artifact["errors"][0]["code"] == "page_limit"


def transform(matrix, point):
    x, y = point
    return tuple(row[0] * x + row[1] * y + row[2] for row in matrix[:2])


@pytest.mark.parametrize("rotation,expected_pdf_origin", [
    (0, (30, 95)), (90, (35, 20)), (180, (210, 25)), (270, (205, 100)),
])
def test_render_crop_respects_rotation_cropbox_and_provenance(tmp_path, rotation, expected_pdf_origin):
    import pypdfium2 as pdfium

    data = make_pdf(tmp_path / "fixture.pdf", rotations=(rotation,), cropbox=(20, 10, 220, 110))
    result, artifact, _ = invoke(tmp_path, "render", data=data,
        extra=("--page", "1", "--crop", "10", "15", "70", "80", "--dpi", "144"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert artifact["image"]["width"] == 120
    assert artifact["image"]["height"] == 130
    assert artifact["page_info"]["intrinsic_rotation_degrees"] == rotation
    image_path = Path(artifact["image"]["path"])
    assert hashlib.sha256(image_path.read_bytes()).hexdigest() == artifact["image"]["sha256"]
    assert artifact["source"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert artifact["verification"]["use_for_calculation"] is False
    inverse = artifact["transforms"]["image_to_pdf"]
    assert transform(inverse, (0, 0)) == pytest.approx(expected_pdf_origin)
    point = (33.5, 56.5)
    assert transform(artifact["transforms"]["pdf_to_image"], transform(inverse, point)) == pytest.approx(point)
    assert transform(artifact["transforms"]["image_to_view"], (0, 0)) == pytest.approx((10, 15))
    # Independent full-page render + pixel crop, versus the adapter's native crop.
    document = pdfium.PdfDocument(data)
    page = document[0]
    bitmap = page.render(scale=2)
    full = bitmap.to_pil().convert("RGB")
    expected = full.crop((20, 30, 140, 160))
    with Image.open(image_path) as actual:
        # PDFium can change antialiasing for a clipped glyph at the bitmap edge
        # (observed within the first three pixels). Content/placement inside
        # the crop and the overall ink bounds must still match the full render.
        assert ImageChops.invert(actual).getbbox() == ImageChops.invert(expected).getbbox()
        assert actual.crop((4, 4, 116, 126)).tobytes() == expected.crop((4, 4, 116, 126)).tobytes()
    expected.close()
    full.close()
    bitmap.close()
    page.close()
    document.close()


@pytest.mark.parametrize("crop", [(0, 0, 241, 120), (20, 20, 10, 30), (0, 0, 0, 1),
                                  (float("nan"), 0, 10, 10)])
def test_invalid_crop_never_writes_image(tmp_path, crop):
    result, artifact, output = invoke(tmp_path, "render", extra=("--page", "1", "--crop", *map(str, crop)))
    assert result.returncode != 0
    assert artifact["errors"][0]["code"] == "invalid_crop"
    assert not list(output.glob("*.png"))


@pytest.mark.parametrize("extra,code", [(("--page", "4"), "page_out_of_bounds"),
    (("--page", "1", "--max-pixels", "10"), "pixel_limit")])
def test_render_limits_are_structured_failures(tmp_path, extra, code):
    result, artifact, output = invoke(tmp_path, "render", extra=extra)
    assert result.returncode != 0
    assert artifact["errors"][0]["code"] == code
    assert not list(output.glob("*.png"))


def test_render_source_mismatch_never_writes_image(tmp_path):
    result, artifact, output = invoke(tmp_path, "render", sha="0" * 64, extra=("--page", "1"))
    assert result.returncode != 0
    assert artifact["errors"][0]["code"] == "source_sha256_mismatch"
    assert not list(output.glob("*.png"))


def test_fractional_crop_records_realized_pixel_offset(tmp_path):
    data = make_pdf(tmp_path / "fixture.pdf", rotations=(0,), cropbox=(20, 10, 220, 110))
    result, artifact, _ = invoke(tmp_path, "render", data=data,
        extra=("--page", "1", "--crop", "10.3", "15.7", "70.2", "80.5", "--dpi", "180"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert artifact["requested_crop"][:2] == [10.3, 15.7]
    assert transform(artifact["transforms"]["image_to_view"], (0, 0)) == pytest.approx((10.4, 16))
    assert transform(artifact["transforms"]["image_to_pdf"], (0, 0)) == pytest.approx((30.4, 94))


def test_source_and_total_text_limits(tmp_path):
    small_input_case = tmp_path / "small-input"
    small_input_case.mkdir()
    result, artifact, _ = invoke(small_input_case, extra=("--max-bytes", "50"))
    assert result.returncode != 0
    assert artifact["errors"][0]["code"] == "source_size_limit"
    assert artifact["source"]["sha256_verified"] is False
    text_case = tmp_path / "text"
    text_case.mkdir()
    result, artifact, _ = invoke(text_case, extra=("--pages", "1,3", "--max-total-text-chars", "10"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert sum(len(p["markdown"]) for p in artifact["text_pages"]) == 10
    assert all(p["text_truncated"] for p in artifact["text_pages"])


def test_deadline_kills_stalled_worker_and_preserves_incomplete_artifact(tmp_path):
    isolated = tmp_path / "isolated-code"
    isolated.mkdir()
    shutil.copyfile(CLI, isolated / "cli.py")
    adapter = (ROOT / "calculator/documents/adapter.py").read_text(encoding="utf-8")
    # Inject a native-call stand-in that hangs in the actual child process;
    # the deadline/kill/artifact path remains the production CLI implementation.
    adapter += "\ndef inspect_document(*args, **kwargs):\n    import time\n    time.sleep(20)\n"
    (isolated / "adapter.py").write_text(adapter, encoding="utf-8")
    data = make_pdf(tmp_path / "source.pdf")
    output = tmp_path / "out"
    start = time.monotonic()
    result = subprocess.run([sys.executable, str(isolated / "cli.py"), "inspect", str(tmp_path / "source.pdf"),
        "--sha256", hashlib.sha256(data).hexdigest(), "--out", str(output), "--timeout", "1"],
        capture_output=True, text=True, timeout=12, check=False)
    elapsed = time.monotonic() - start
    artifact = json.loads((output / "inspect.json").read_text(encoding="utf-8"))
    assert result.returncode == 124
    assert elapsed < 10
    assert artifact["complete"] is False
    assert artifact["source"]["sha256_verified"] is True
    assert artifact["errors"][-1]["code"] == "worker_timeout"
