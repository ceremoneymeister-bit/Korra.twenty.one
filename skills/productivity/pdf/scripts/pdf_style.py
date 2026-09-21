#!/usr/bin/env python3
"""Copy an editable Korra document kit and print local HTML with Chromium."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


KIT = Path(__file__).resolve().parents[1] / "templates" / "korra"
TEMPLATES = ("brief", "proposal", "report", "guide", "presentation")


def initialize(template: str, output_dir: Path, *, company: str | None = None) -> dict:
    """Create a portable copy without overwriting a previous document."""
    if template not in TEMPLATES:
        raise ValueError(f"Unknown template: {template}")
    if company is not None:
        company = " ".join(company.split())
        if not company or not company.isprintable():
            raise ValueError("Company name must contain printable text.")
    output_dir = output_dir.expanduser().absolute()
    if output_dir.exists():
        raise FileExistsError(f"Directory already exists: {output_dir}. Choose a new directory.")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    # Stage the complete kit before exposing it, including the font license.
    with tempfile.TemporaryDirectory(prefix=".korra-kit-", dir=output_dir.parent) as temporary:
        staged = Path(temporary) / "document"
        staged.mkdir()
        shutil.copy2(KIT / f"{template}.html", staged / "document.html")
        for name in ("korra.css", "theme.css"):
            shutil.copy2(KIT / name, staged / name)
        shutil.copytree(KIT / "fonts", staged / "fonts")
        if company is None:
            shutil.copytree(KIT / "brand", staged / "brand")
        else:
            # Names are data, including quotes, ampersands and HTML/CSS syntax.
            # A company without a supplied logo gets its actual name in type.
            document = staged / "document.html"
            content = document.read_text(encoding="utf-8")
            escaped = html.escape(company)
            content = re.sub(
                r'<img class="brand" src="brand/korra-on-(?:light|dark)\.png" alt="Korra">',
                lambda _: f'<div class="brand-name">{escaped}</div>', content,
            )
            content = content.replace(" — Korra</title>", f" — {escaped}</title>")
            document.write_text(content, encoding="utf-8")
            # CSS hex escapes are valid for every character and cannot break
            # out of a quoted margin-box label, even for adversarial input.
            label = '"' + "".join(f"\\{ord(char):x} " for char in company) + '"'
            for name in ("korra.css", "theme.css"):
                path = staged / name
                css = path.read_text(encoding="utf-8")
                path.write_text(css.replace('content: "KORRA";', f"content: {label};"), encoding="utf-8")
        # copytree also refuses a destination created concurrently.
        shutil.copytree(staged, output_dir)
    return {"html": str(output_dir / "document.html"), "template": template,
            "editable_theme": str(output_dir / "theme.css"), "company": company or "Korra"}


def _executable(value: str) -> str | None:
    path = Path(value).expanduser()
    if path.is_file() and os.access(path, os.X_OK):
        return str(path.resolve())
    return shutil.which(value)


def _browser_cache_roots() -> list[Path]:
    roots = []
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if configured and configured != "0":
        roots.append(Path(configured).expanduser())
    roots.append(Path.home() / ".cache" / "ms-playwright")
    if sys.platform == "darwin":
        roots.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        roots.append(local / "ms-playwright")
    return roots


def find_browser(explicit: str | None = None) -> str:
    """Reuse the product's installed browser; never download on first use."""
    configured = explicit or os.environ.get("AGENT_BROWSER_EXECUTABLE_PATH")
    if configured:
        found = _executable(configured)
        if found:
            return found
        raise FileNotFoundError(f"Configured Chromium executable is unavailable: {configured}")
    # The product image installs only headless-shell into PLAYWRIGHT_BROWSERS_PATH.
    patterns = (
        "chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell*",
        "chromium_headless_shell-*/chrome-*/headless_shell*",
        "chromium-*/chrome-*/chrome", "chromium-*/chrome-*/chrome.exe",
        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    )
    for root in _browser_cache_roots():
        candidates = {p for pattern in patterns for p in root.glob(pattern)}
        # Numeric build order: chromium-1000 is newer than chromium-999.
        def build_number(path: Path) -> int:
            match = re.search(r"(?:chromium|chromium_headless_shell)-(\d+)", str(path))
            return int(match.group(1)) if match else 0
        for path in sorted(candidates, key=lambda p: (build_number(p), str(p)), reverse=True):
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.resolve())
    for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(command)
        if found:
            return found
    locations = []
    if sys.platform == "darwin":
        locations.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    if sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(variable):
                locations.append(Path(os.environ[variable]) / "Google/Chrome/Application/chrome.exe")
    for path in locations:
        if path.is_file():
            return str(path)
    raise FileNotFoundError(
        "Chromium was not found. Use the installed Korra browser or supply --browser PATH "
        "to Chrome/Chromium 131+. No browser or package was downloaded."
    )


def render(source: Path, output: Path, *, browser: str | None = None,
           timeout: float = 60, no_sandbox: bool = False, force: bool = False) -> dict:
    source = source.expanduser().resolve()
    output = output.expanduser().absolute()
    if not source.is_file() or source.suffix.lower() not in (".html", ".htm"):
        raise ValueError("Input must be an existing local HTML file.")
    if output.suffix.lower() != ".pdf":
        raise ValueError("Output must have a .pdf extension.")
    if timeout <= 0:
        raise ValueError("Timeout must be positive.")
    if output.exists() and not force:
        raise FileExistsError(f"Output already exists: {output}. Use --force to replace it.")
    executable = find_browser(browser)
    output.parent.mkdir(parents=True, exist_ok=True)
    # An isolated profile leaves existing browser sessions alone. The final PDF
    # is replaced only after a successful print, on the same filesystem.
    with tempfile.TemporaryDirectory(prefix=".korra-pdf-", dir=output.parent) as temporary:
        staging = Path(temporary)
        printed = staging / "printed.pdf"
        command = [executable, "--headless", "--no-pdf-header-footer",
                   "--disable-background-networking", "--disable-extensions",
                   "--disable-sync", "--no-first-run", "--disable-dev-shm-usage",
                   f"--user-data-dir={staging / 'browser-profile'}",
                   f"--print-to-pdf={printed}", "--virtual-time-budget=2000"]
        if no_sandbox:
            command.append("--no-sandbox")
        command.append(source.as_uri())
        try:
            process = subprocess.run(command, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"PDF printing exceeded {timeout:g}s; previous output is unchanged.") from exc
        if process.returncode != 0 or not printed.is_file():
            detail = process.stderr.strip()[-1800:]
            raise RuntimeError(f"Chromium did not produce a PDF (exit {process.returncode}). {detail}")
        with printed.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise RuntimeError("Chromium output is not a PDF; previous output is unchanged.")
        if force:
            os.replace(printed, output)
        else:
            # Atomic create-if-absent, including a concurrent writer. Both files
            # are on the same filesystem; the staging link is removed on exit.
            os.link(printed, output)
    return {"pdf": str(output), "html": str(source), "bytes": output.stat().st_size,
            "browser": executable, "sandbox": not no_sandbox,
            "verification": "printed; text and page images still need review"}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Copy a starter with local CSS, font, and license")
    init.add_argument("template", choices=TEMPLATES)
    init.add_argument("--output-dir", type=Path, required=True)
    init.add_argument("--company", help="Known company name for header, PDF title and footer; defaults to Korra")
    pdf = commands.add_parser("render", help="Print an edited local HTML document")
    pdf.add_argument("html", type=Path)
    pdf.add_argument("-o", "--output", type=Path, required=True)
    pdf.add_argument("--browser", help="Existing Chrome/Chromium 131+ executable")
    pdf.add_argument("--timeout", type=float, default=60)
    pdf.add_argument("--force", action="store_true", help="Replace an existing PDF after successful printing")
    pdf.add_argument("--no-sandbox", action="store_true",
                     help="Only for trusted local documents in an isolated container that requires it")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(args.template, args.output_dir, company=args.company)
        else:
            result = render(args.html, args.output, browser=args.browser, timeout=args.timeout,
                            no_sandbox=args.no_sandbox, force=args.force)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
