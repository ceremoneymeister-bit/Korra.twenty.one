"""Dependency-free kernel bridge to the disposable document CLI worker.

Only a trusted order resolver may supply source_path. This module does not grant
filesystem access or resolve order IDs. See README.md for the public result.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys


READER_VERSION = "calc21-documents-v2;pdf-inspector=1.17.0;pypdfium2=5.13.0;pillow=12.3.0;xlsx=1;defusedxml=0.7.1"
CLI = Path(__file__).with_name("cli.py")
MAX_ARTIFACT_BYTES = 64 * 1024**2
COMMON_DEFAULTS = {"timeout": 60, "max_bytes": 100 * 1024**2, "max_pages": 1000,
                   "max_sheets": 128, "max_cells": 20000, "max_zip_members": 5000,
                   "max_expanded_bytes": 128 * 1024**2, "max_member_bytes": 64 * 1024**2}
INSPECT_DEFAULTS = {"pages": None, "max_text_chars": 8000, "max_total_text_chars": 40000}
RENDER_DEFAULTS = {"page": None, "dpi": 180, "crop": None, "max_pixels": 16_000_000}
CEILINGS = {"max_bytes": 200 * 1024**2, "max_pages": 2000, "max_sheets": 256,
            "max_cells": 100000, "max_zip_members": 10000,
            "max_expanded_bytes": 512 * 1024**2, "max_member_bytes": 128 * 1024**2,
            "max_text_chars": 20000, "max_total_text_chars": 200000,
            "max_pixels": 40_000_000, "page": 2000, "dpi": 600}


def canonical_options(command: str = "inspect", options: dict | None = None) -> dict:
    if command not in {"inspect", "render"}:
        raise ValueError("unsupported_command")
    defaults = COMMON_DEFAULTS | (INSPECT_DEFAULTS if command == "inspect" else RENDER_DEFAULTS)
    if options is not None and (not isinstance(options, dict) or set(options) - set(defaults)):
        raise ValueError("invalid_options")
    result = defaults | (options or {})
    for key, value in result.items():
        if key in CEILINGS:
            if type(value) is not int or not 1 <= value <= CEILINGS[key]:
                raise ValueError("invalid_options")
        elif key == "timeout":
            if type(value) not in {int, float} or not math.isfinite(value) or not 1 <= value <= 180:
                raise ValueError("invalid_options")
            result[key] = float(value)
        elif key == "pages" and value is not None:
            if not isinstance(value, str) or len(value) > 300 or not re.fullmatch(r"[0-9,\s-]+", value):
                raise ValueError("invalid_options")
        elif key == "crop" and value is not None:
            if (not isinstance(value, (list, tuple)) or len(value) != 4
                    or any(type(item) not in {int, float} or not math.isfinite(item) for item in value)):
                raise ValueError("invalid_options")
            result[key] = [float(item) for item in value]
    if command == "render" and result["dpi"] < 50:
        raise ValueError("invalid_options")
    return result


def reader_fingerprint(command: str = "inspect", options: dict | None = None) -> str:
    recipe = {"version": READER_VERSION, "command": command,
              "options": canonical_options(command, options)}
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


READER_FINGERPRINT = reader_fingerprint()


def _error(result: dict, code: str) -> dict:
    result["complete"] = False
    result["status"] = "failed"
    result["errors"].append({"code": code, "message": code.replace("_", " ")})
    return result


def _source(source: dict) -> dict:
    if not isinstance(source, dict):
        raise ValueError("invalid_source_metadata")
    identifier, sha, size, relative = (source.get(key) for key in ("source_id", "sha256", "bytes", "relative_path"))
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
        raise ValueError("invalid_source_metadata")
    if not isinstance(sha, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", sha):
        raise ValueError("invalid_source_metadata")
    if type(size) is not int or size < 0:
        raise ValueError("invalid_source_metadata")
    if (not isinstance(relative, str) or not relative or len(relative) > 4096
            or relative.startswith("/") or "\\" in relative or "\x00" in relative
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise ValueError("invalid_source_metadata")
    return {"source_id": identifier, "sha256": sha.lower(), "bytes": size,
            "relative_path": relative, "sha256_verified": False}


def _normalize(artifact: dict, result: dict, destination: Path) -> dict:
    source = result["source"]
    actual_source = artifact.get("source", {})
    source["sha256_verified"] = (actual_source.get("sha256_verified") is True
                                  and actual_source.get("sha256") == source["sha256"]
                                  and actual_source.get("bytes") == source["bytes"])
    # Export known data fields; local request/source/output paths never cross the bridge.
    for key in ("inventory", "text_pages", "sheets", "page_count", "sheet_count", "page",
                "page_info", "nominal_dpi", "requested_crop", "coordinate_frames", "transforms",
                "source_detection", "external_relationships_ignored", "formulas_evaluated",
                "external_links_followed", "uninspected_page_count", "uninspected_sheet_count"):
        if key in artifact:
            result[key] = artifact[key]
    result["reader"]["parser_versions"] = artifact.get("parser_versions", {})
    expected = ({"pdf-inspector": "1.17.0", "pypdfium2": "5.13.0", "pillow": "12.3.0"}
                if result["document_type"] == "pdf" else {"xlsx_reader": "1", "defusedxml": "0.7.1"})
    versions = result["reader"]["parser_versions"]
    if versions and any(versions.get(key) != value for key, value in expected.items()):
        return _error(result, "reader_version_mismatch")
    for row in result.get("sheets", []):
        for cell in row.get("cells", []):
            cell["provenance"]["source_id"] = source["source_id"]
    for error in artifact.get("errors", []):
        code = error.get("code", "document_processing_error")
        if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{1,100}", code):
            code = "document_processing_error"
        normalized = {"code": code, "message": code.replace("_", " ")}
        for key in ("page", "sheet"):
            if type(error.get(key)) is int:
                normalized[key] = error[key]
        result["errors"].append(normalized)
    if "image" in artifact:
        supplied = artifact["image"]
        image_path = Path(supplied.get("path", ""))
        if not image_path.is_absolute():
            image_path = destination / image_path
        if (image_path.parent != destination or image_path.is_symlink()
                or not re.fullmatch(r"page-[0-9]{4}\.png", image_path.name)
                or not image_path.is_file() or image_path.stat().st_size > MAX_ARTIFACT_BYTES):
            return _error(result, "invalid_image_artifact")
        if hashlib.sha256(image_path.read_bytes()).hexdigest() != supplied.get("sha256"):
            return _error(result, "image_sha256_mismatch")
        result["image"] = {"path": image_path.name, "sha256": supplied["sha256"],
                           "bytes": image_path.stat().st_size,
                           "width": supplied["width"], "height": supplied["height"]}
    text_pages = artifact.get("text_pages", [])
    text_truncated = artifact.get("text_truncated", False) or any(row.get("text_truncated", False) for row in text_pages)
    result["coverage"] = {
        "inventory_complete": artifact.get("inventory_complete", False),
        "pages_total": artifact.get("page_count"),
        "pages_inventoried": sum(row.get("status") == "inspected" for row in artifact.get("inventory", [])),
        "pages_accounted": len(artifact.get("inventory", [])),
        "selected_text_pages": artifact.get("selected_text_pages", []),
        "text_pages_read": [row["page"] for row in text_pages if row.get("status") == "extracted_unverified"],
        "text_truncated": bool(text_truncated),
        "sheets_total": artifact.get("sheet_count"),
        "sheets_inventoried": sum(row.get("status") != "pending" for row in artifact.get("sheets", [])),
        "cells_read": artifact.get("cells_read", 0), "cells_complete": artifact.get("cells_complete", False),
        "rendered_pages": [artifact["page"]] if "image" in result else [],
    }
    unsupported = any(error["code"] in {"unsupported_format", "unsupported_command",
                       "xlsx_macros_unsupported", "xlsx_namespace_unsupported", "xlsx_encrypted"}
                      for error in result["errors"])
    complete = artifact.get("complete") is True and source["sha256_verified"] and not result["errors"] and not text_truncated
    observed = bool(result.get("inventory") or result.get("sheets") or result.get("image"))
    result.update(complete=complete, status="complete" if complete else "unsupported" if unsupported
                  else "partial" if observed else "failed")
    return result


def read_document(source_path: Path, source: dict, output_dir: Path, *, command: str = "inspect",
                  options: dict | None = None, python: str | None = None) -> dict:
    """Inspect/render one resolved revision; always return an unverified typed result."""
    result = {"schema_version": 2, "command": command, "status": "failed", "complete": False,
              "document_type": "unsupported", "source": {}, "use_for_calculation": False,
              "verification": {"numeric_facts": "unverified", "numeric_confidence": None,
                               "use_for_calculation": False},
              "reader": {"version": READER_VERSION, "fingerprint": None, "options": {}},
              "coverage": {"inventory_complete": False}, "errors": []}
    try:
        result["source"] = _source(source)
        normalized_options = canonical_options(command, options)
        result["reader"].update(options=normalized_options, fingerprint=reader_fingerprint(command, normalized_options))
    except ValueError as exc:
        return _error(result, str(exc))
    kind = {".pdf": "pdf", ".xlsx": "xlsx"}.get(PurePosixPath(source["relative_path"]).suffix.lower(), "unsupported")
    result["document_type"] = kind
    destination = Path(output_dir).absolute()
    try:
        if destination.is_symlink():
            return _error(result, "invalid_output_directory")
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(destination.iterdir()):
            return _error(result, "output_directory_not_empty")
        # Reserve the fresh attempt against accidental concurrent reuse.
        with (destination / ".attempt").open("x"):
            pass
    except OSError:
        return _error(result, "invalid_output_directory")
    artifact_path = destination / f"{command}.json"
    limit_names = set(CEILINGS) - {"page", "dpi"}
    request = {"command": command, "source": str(Path(source_path).absolute()),
               "sha256": result["source"]["sha256"], "expected_bytes": result["source"]["bytes"],
               "document_type": kind, "out": str(destination), **normalized_options,
               "limits": {key: value for key, value in normalized_options.items() if key in limit_names}}
    default_python = CLI.parents[2] / ".venv/bin/python"
    interpreter = python or (str(default_python) if (CLI.parents[2] / ".venv/.calculator-documents-venv").is_file()
                            and default_python.is_file() else sys.executable)
    try:
        process = subprocess.Popen([interpreter, str(CLI), "_worker"], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True,
                                   env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONUTF8": "1"})
    except OSError:
        return _error(result, "reader_unavailable")
    failure = None
    try:
        process.communicate(json.dumps(request, allow_nan=False).encode(), timeout=normalized_options["timeout"])
    except subprocess.TimeoutExpired:
        failure = "worker_timeout"
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
    try:
        if artifact_path.is_symlink() or artifact_path.stat().st_size > MAX_ARTIFACT_BYTES:
            return _error(result, "invalid_reader_artifact")
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        if not isinstance(artifact, dict):
            return _error(result, "invalid_reader_artifact")
        if failure or process.returncode not in {0, 2}:
            artifact.setdefault("errors", []).append({"code": failure or "worker_exit"})
            artifact["complete"] = False
        return _normalize(artifact, result, destination)
    except (OSError, ValueError, TypeError, KeyError):
        return _error(result, failure or ("worker_exit" if process.returncode not in {0, 2}
                                         else "invalid_reader_artifact"))
