"""Portable Korra document copies, safe printing, and actual browser acceptance."""

from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

import pytest


SKILL = Path(__file__).resolve().parents[2] / "skills" / "productivity" / "pdf"
SPEC = importlib.util.spec_from_file_location("pdf_style", SKILL / "scripts" / "pdf_style.py")
pdf_style = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pdf_style)


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []
        self.tags = []
        self.text = []

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.paths.append(attrs["href"])
        if tag in ("img", "script") and attrs.get("src"):
            self.paths.append(attrs["src"])


@pytest.mark.parametrize("template", pdf_style.TEMPLATES)
def test_initialized_document_is_portable_and_editable(tmp_path, template):
    directory = tmp_path / "Документ с пробелами"
    result = pdf_style.initialize(template, directory)
    parser = Resources()
    parser.feed(Path(result["html"]).read_text(encoding="utf-8"))
    assert parser.paths, "Template needs its local styles"
    for resource in parser.paths:
        assert not urlsplit(resource).scheme
        path = directory / unquote(resource)
        assert path.is_file()
        if path.suffix == ".css":
            for linked in re.findall(r'url\([\'"]?([^\)\'\"]+)', path.read_text(encoding="utf-8")):
                assert not urlsplit(linked).scheme
                assert (path.parent / linked).is_file()
    assert (directory / "fonts/Onest-OFL.txt").is_file()
    theme = Path(result["editable_theme"])
    theme.write_text(":root { --accent: #ffd600; }", encoding="utf-8")
    assert (pdf_style.KIT / "theme.css").read_text(encoding="utf-8") != theme.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        pdf_style.initialize(template, directory)
    assert "#ffd600" in theme.read_text(encoding="utf-8")


@pytest.mark.parametrize("template", pdf_style.TEMPLATES)
def test_company_name_is_text_and_removes_korra_brand(tmp_path, template):
    company = 'ООО "Север & Юг" <script>alert(1)</script> \\ край'
    result = pdf_style.initialize(template, tmp_path / "company-document", company=company)
    parser = Resources()
    parser.feed(Path(result["html"]).read_text(encoding="utf-8"))
    assert company in parser.text
    assert "script" not in parser.tags
    assert "Korra" not in "".join(parser.text)
    assert not any("korra-on-" in path for path in parser.paths)
    assert not (tmp_path / "company-document/brand").exists()
    assert result["company"] == company
    assert 'content: "KORRA"' not in Path(result["editable_theme"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["  ", "Компания\0"])
def test_invalid_company_name_leaves_no_partial_copy(tmp_path, name):
    output = tmp_path / "company-document"
    with pytest.raises(ValueError, match="Company name"):
        pdf_style.initialize("brief", output, company=name)
    assert not output.exists()


@pytest.mark.parametrize("layout", [
    "chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell",
    "chromium_headless_shell-1234/chrome-linux/headless_shell",
    "chromium_headless_shell-1234/chrome-headless-shell-win64/chrome-headless-shell.exe",
    "chromium-1234/chrome-linux64/chrome",
    "chromium-1234/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
])
def test_finds_browser_in_installed_product_cache(tmp_path, monkeypatch, layout):
    executable = tmp_path / layout
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"browser fixture")
    executable.chmod(0o755)
    monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
    monkeypatch.setattr(pdf_style, "_browser_cache_roots", lambda: [tmp_path])
    assert pdf_style.find_browser() == str(executable)


def test_explicit_missing_browser_does_not_silently_switch(tmp_path):
    with pytest.raises(FileNotFoundError, match="Configured Chromium"):
        pdf_style.find_browser(str(tmp_path / "not-installed"))


@pytest.mark.parametrize("outcome", ["failed", "timeout", "invalid", "success", "raced"])
def test_failed_print_preserves_old_pdf_and_success_replaces_atomically(tmp_path, monkeypatch, outcome):
    source = tmp_path / "Документ ' с пробелами.html"
    source.write_text("<p>Кириллица</p>", encoding="utf-8")
    output = tmp_path / "out.pdf"
    old = b"%PDF-previous"
    if outcome != "raced":
        output.write_bytes(old)
    monkeypatch.setattr(pdf_style, "find_browser", lambda _: "installed-chromium")

    def fake_run(command, **kwargs):
        assert isinstance(command, list)
        assert command[-1] == source.as_uri()
        assert "shell" not in kwargs
        assert "--no-sandbox" not in command
        staged = Path(next(arg.partition("=")[2] for arg in command if arg.startswith("--print-to-pdf=")))
        assert staged.parent.parent == output.parent
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if outcome == "failed":
            return subprocess.CompletedProcess(command, 1, "", "browser failed")
        staged.write_bytes(b"not a PDF" if outcome == "invalid" else b"%PDF-new")
        if outcome == "raced":
            output.write_bytes(old)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(pdf_style.subprocess, "run", fake_run)
    if outcome == "success":
        pdf_style.render(source, output, force=True)
        assert output.read_bytes() == b"%PDF-new"
    else:
        with pytest.raises((RuntimeError, FileExistsError)):
            pdf_style.render(source, output, force=outcome != "raced")
        assert output.read_bytes() == old
    assert not list(tmp_path.glob(".korra-pdf-*"))


def test_cli_reports_error_without_overwriting_user_work(tmp_path):
    directory = tmp_path / "Мой документ"
    directory.mkdir()
    result = subprocess.run(
        [sys.executable, str(SKILL / "scripts/pdf_style.py"), "init", "brief",
         "--output-dir", str(directory)], capture_output=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 1
    assert "already exists" in json.loads(result.stderr)["error"]
    assert list(directory.iterdir()) == []


def test_bundled_sync_ships_assets_updates_old_skill_and_preserves_customized_theme(tmp_path, monkeypatch):
    from tools import skills_sync

    bundled = tmp_path / "bundle"
    source = bundled / "productivity/pdf"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: pdf\nversion: 1.1.0\n---\nOld PDF skill\n", encoding="utf-8")
    profile = tmp_path / "isolated-profile"
    installed = profile / "skills"
    monkeypatch.setattr(skills_sync, "HERMES_HOME", profile)
    monkeypatch.setattr(skills_sync, "SKILLS_DIR", installed)
    monkeypatch.setattr(skills_sync, "MANIFEST_FILE", installed / ".bundled_manifest")
    monkeypatch.setattr(skills_sync, "_get_bundled_dir", lambda: bundled)
    monkeypatch.setattr(skills_sync, "_get_optional_dir", lambda: tmp_path / "optional")
    monkeypatch.setattr(skills_sync, "_build_external_skill_index", lambda: set())
    monkeypatch.setattr(skills_sync, "_read_suppressed_names", lambda: set())
    result = skills_sync.sync_skills(quiet=True)
    assert "pdf" in result["copied"]
    assert not (installed / "productivity/pdf/templates").exists()
    shutil.copytree(SKILL, source, dirs_exist_ok=True)
    result = skills_sync.sync_skills(quiet=True)
    assert "pdf" in result["updated"]
    kit = installed / "productivity/pdf/templates/korra"
    assert (kit / "fonts/Onest-Variable.woff2").read_bytes() == (pdf_style.KIT / "fonts/Onest-Variable.woff2").read_bytes()
    assert (kit / "proposal.html").is_file()
    theme = kit / "theme.css"
    theme.write_text("/* user customization */", encoding="utf-8")
    (source / "templates/korra/theme.css").write_text("/* next release */", encoding="utf-8")
    result = skills_sync.sync_skills(quiet=True)
    assert "pdf" in result["user_modified"]
    assert theme.read_text(encoding="utf-8") == "/* user customization */"
    # A second fresh profile receives the full package without a separate install.
    fresh = tmp_path / "fresh-profile"
    monkeypatch.setattr(skills_sync, "HERMES_HOME", fresh)
    monkeypatch.setattr(skills_sync, "SKILLS_DIR", fresh / "skills")
    monkeypatch.setattr(skills_sync, "MANIFEST_FILE", fresh / "skills/.bundled_manifest")
    result = skills_sync.sync_skills(quiet=True)
    assert "pdf" in result["copied"]
    assert (fresh / "skills/productivity/pdf/scripts/pdf_style.py").is_file()
    assert (fresh / "skills/productivity/pdf/templates/korra/fonts/Onest-Variable.woff2").is_file()


@pytest.fixture
def browser_acceptance():
    """Optional integration prerequisites; no downloads and no provider calls."""
    if not shutil.which("pdfinfo") or not shutil.which("pdftotext"):
        pytest.skip("PDF acceptance needs local poppler utilities")
    try:
        return pdf_style.find_browser()
    except FileNotFoundError:
        pytest.skip("PDF acceptance needs installed Chromium 131+")


@pytest.mark.parametrize("template,pages,last_text", [
    ("brief", 1, "Выбрать документ и ожидаемый ответ"),
    ("proposal", 2, "Взять один реальный документ"),
    ("report", 1, "Повторить проблемный этап"),
    ("guide", 1, "Вы получили нужные факты"),
    ("presentation", 3, "Возьмите одно предложение"),
])
def test_actual_pdf_has_expected_pages_and_selectable_cyrillic(tmp_path, browser_acceptance, template, pages, last_text):
    result = pdf_style.initialize(template, tmp_path / "Документ с пробелами")
    output = tmp_path / "result.pdf"
    pdf_style.render(Path(result["html"]), output, browser=browser_acceptance,
                     no_sandbox=hasattr(os, "geteuid") and os.geteuid() == 0)
    info = subprocess.run(["pdfinfo", str(output)], capture_output=True, encoding="utf-8", check=True, timeout=10).stdout
    assert int(re.search(r"Pages:\s+(\d+)", info).group(1)) == pages
    if template == "presentation":
        width, height = map(float, re.search(r"Page size:\s+([\d.]+) x ([\d.]+)", info).groups())
        assert width / height == pytest.approx(16 / 9, rel=.003)
    text = subprocess.run(["pdftotext", str(output), "-"], capture_output=True, encoding="utf-8", check=True, timeout=10).stdout
    normalized = " ".join(text.split())
    assert last_text in normalized
    assert "\ufffd" not in text
    assert "file:///" not in text


@pytest.mark.parametrize("template,expected_pages", [("brief", 1), ("presentation", 3)])
def test_company_pdf_has_its_name_in_pages_and_metadata(tmp_path, browser_acceptance, template, expected_pages):
    company = 'Мастерская «Север & Юг»'
    command = [sys.executable, str(SKILL / "scripts/pdf_style.py"), "init", template,
               "--company", company, "--output-dir", str(tmp_path / "company")]
    initialized = subprocess.run(command, capture_output=True, encoding="utf-8", check=True, timeout=10)
    source = Path(json.loads(initialized.stdout)["html"])
    output = tmp_path / "company.pdf"
    pdf_style.render(source, output, browser=browser_acceptance,
                     no_sandbox=hasattr(os, "geteuid") and os.geteuid() == 0)
    info = subprocess.run(["pdfinfo", str(output)], capture_output=True, encoding="utf-8", check=True, timeout=10).stdout
    assert company in info
    assert int(re.search(r"Pages:\s+(\d+)", info).group(1)) == expected_pages
    text = subprocess.run(["pdftotext", str(output), "-"], capture_output=True, encoding="utf-8", check=True, timeout=10).stdout
    assert "KORRA" not in text.upper()
    assert text.count(company) == (2 if template == "brief" else 3)


def test_long_table_preserves_all_rows_and_repeats_header(tmp_path, browser_acceptance):
    result = pdf_style.initialize("report", tmp_path / "long-table")
    source = Path(result["html"])
    rows = "".join(
        f"<tr><td>ROW_{index:03d}</td><td>Кириллица: проверяем длинную таблицу и переносы строк.</td></tr>"
        for index in range(90)
    )
    source.write_text(
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<title>Таблица</title><link rel="stylesheet" href="korra.css">'
        '<h1>Проверка таблицы</h1><table><thead><tr><th>ROW_ID</th><th>Наблюдение</th></tr></thead>'
        f'<tbody>{rows}</tbody></table><p>Конец документа: END_OF_DOCUMENT.</p></html>',
        encoding="utf-8",
    )
    output = tmp_path / "long-table.pdf"
    pdf_style.render(source, output, browser=browser_acceptance,
                     no_sandbox=hasattr(os, "geteuid") and os.geteuid() == 0)
    text = subprocess.run(["pdftotext", str(output), "-"], capture_output=True,
                          encoding="utf-8", check=True, timeout=10).stdout
    for index in range(90):
        assert text.count(f"ROW_{index:03d}") == 1
    assert text.count("ROW_ID") >= 3
    assert "END_OF_DOCUMENT." in text
