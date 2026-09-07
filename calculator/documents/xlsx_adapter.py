"""Bounded XLSX observations; formulae, links and embedded code are never run."""
from __future__ import annotations

from importlib.metadata import version
from io import BytesIO
import posixpath
import re
from zipfile import BadZipFile, ZipFile

from adapter import DocumentError, Limits, add_error

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"s": MAIN}
XLSX_READER_VERSION = "1"


def _xml(archive: ZipFile, name: str, limits: Limits):
    from defusedxml.ElementTree import fromstring
    from defusedxml.common import DefusedXmlException

    try:
        with archive.open(name) as stream:
            payload = stream.read(limits.max_member_bytes + 1)
        if len(payload) > limits.max_member_bytes:
            raise DocumentError("xlsx_member_limit", "Expanded XML member exceeds limit")
        return fromstring(payload, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise DocumentError("xlsx_unsafe_xml", "DTD/entity declarations are unsupported") from exc
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("xlsx_corrupt_xml", "Required XLSX XML part is missing or invalid") from exc


def _check_archive(archive: ZipFile, limits: Limits) -> None:
    members = archive.infolist()
    if len(members) > limits.max_zip_members:
        raise DocumentError("xlsx_member_count_limit", "ZIP member count exceeds limit")
    names = set()
    expanded = 0
    for member in members:
        name = member.filename
        if (name in names or name.startswith("/") or "\\" in name
                or any(part in {".", ".."} for part in name.split("/"))):
            raise DocumentError("xlsx_unsafe_archive", "Duplicate or unsafe ZIP member path")
        names.add(name)
        if member.flag_bits & 1:
            raise DocumentError("xlsx_encrypted", "Encrypted ZIP members are unsupported")
        if member.file_size > limits.max_member_bytes:
            raise DocumentError("xlsx_member_limit", "Expanded ZIP member exceeds limit")
        expanded += member.file_size
        if expanded > limits.max_expanded_bytes:
            raise DocumentError("xlsx_expansion_limit", "ZIP expanded byte count exceeds limit")
        if name.lower().endswith("vbaproject.bin"):
            raise DocumentError("xlsx_macros_unsupported", "Macro-enabled workbooks are unsupported")


def _column_number(letters: str) -> int:
    result = 0
    for letter in letters:
        result = result * 26 + ord(letter) - ord("A") + 1
    return result


def _coordinate(value: str) -> str:
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", value)
    if not match or _column_number(match[1]) > 16384 or int(match[2]) > 1048576:
        raise DocumentError("xlsx_invalid_cell", "Cell address is missing or outside the XLSX grid")
    return value


def _text(element) -> str:
    if element is None:
        return ""
    # Phonetic runs (rPh) are annotations, not part of the stored cell value.
    parts = []
    for node in element:
        if node.tag == f"{{{MAIN}}}t":
            parts.append(node.text or "")
        elif node.tag == f"{{{MAIN}}}r":
            parts.append(node.findtext("s:t", default="", namespaces=NS))
    return "".join(parts)


def inspect_xlsx(data: bytes, artifact: dict, limits: Limits,
                 checkpoint=lambda artifact: None) -> dict:
    artifact.update(parser_versions={"xlsx_reader": XLSX_READER_VERSION,
                                     "defusedxml": version("defusedxml")},
                    sheets=[], sheet_count=None, inventory_complete=False,
                    cells_complete=False, cells_read=0, text_truncated=False,
                    external_links_followed=False, formulas_evaluated=False)
    try:
        archive = ZipFile(BytesIO(data))
    except BadZipFile as exc:
        raise DocumentError("invalid_xlsx", "Source is not an XLSX ZIP package") from exc
    with archive:
        _check_archive(archive, limits)
        content_types = _xml(archive, "[Content_Types].xml", limits)
        if any("macroEnabled" in entry.get("ContentType", "")
               or "vbaProject" in entry.get("ContentType", "") for entry in content_types):
            raise DocumentError("xlsx_macros_unsupported", "Macro-enabled workbooks are unsupported")
        workbook = _xml(archive, "xl/workbook.xml", limits)
        if workbook.tag != f"{{{MAIN}}}workbook":
            raise DocumentError("xlsx_namespace_unsupported", "Unsupported XLSX workbook namespace")
        rels = _xml(archive, "xl/_rels/workbook.xml.rels", limits)
        if rels.tag != f"{{{PACKAGE}}}Relationships":
            raise DocumentError("xlsx_corrupt_xml", "Invalid relationship package namespace")
        relationships = {}
        for relation in rels:
            identifier = relation.get("Id")
            if identifier in relationships:
                raise DocumentError("xlsx_duplicate_relationship", "Duplicate relationship identifier")
            relationships[identifier] = relation.attrib
        elements = workbook.findall("s:sheets/s:sheet", NS)
        if (not elements or any(not sheet.get("name") or len(sheet.get("name", "")) > 1024 for sheet in elements)
                or len({sheet.get("name") for sheet in elements}) != len(elements)
                or len({sheet.get("sheetId") for sheet in elements}) != len(elements)):
            raise DocumentError("xlsx_invalid_workbook", "Missing or ambiguous worksheet identity")
        artifact["sheet_count"] = len(elements)
        artifact["sheets"] = [{"sheet": index + 1, "name": sheet.get("name", ""),
                               "state": sheet.get("state", "visible"), "status": "pending",
                               "cells": [], "cells_complete": False}
                              for index, sheet in enumerate(elements[:limits.max_sheets])]
        artifact["uninspected_sheet_count"] = len(elements)
        checkpoint(artifact)
        if len(elements) > limits.max_sheets:
            raise DocumentError("xlsx_sheet_limit", "Workbook sheet count exceeds limit")
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = _xml(archive, "xl/sharedStrings.xml", limits)
            for item in shared.findall("s:si", NS):
                if len(strings) >= limits.max_cells:
                    raise DocumentError("xlsx_shared_string_limit", "Shared string count exceeds cell limit")
                strings.append(_text(item))
        remaining = limits.max_total_text_chars
        external = sum(1 for relation in relationships.values()
                       if relation.get("TargetMode") == "External")
        artifact["external_relationships_ignored"] = external
        for index, (sheet, row) in enumerate(zip(elements, artifact["sheets"])):
            try:
                relation = relationships.get(sheet.get(f"{{{RELS}}}id"), {})
                if relation.get("TargetMode") == "External":
                    raise DocumentError("xlsx_external_sheet", "External sheet target is unsupported")
                if relation.get("Type") != f"{RELS}/worksheet":
                    raise DocumentError("xlsx_sheet_type_unsupported", "Only worksheet cell content is supported")
                target = relation.get("Target", "")
                target = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                                           else posixpath.join("xl", target))
                if target.startswith("../") or ":" in target or "\\" in target:
                    raise DocumentError("xlsx_unsafe_relationship", "Unsafe internal sheet target")
                document = _xml(archive, target, limits)
                if document.tag != f"{{{MAIN}}}worksheet":
                    raise DocumentError("xlsx_sheet_type_unsupported", "Unsupported worksheet namespace")
                row["declared_dimension"] = (document.find("s:dimension", NS).get("ref")
                                             if document.find("s:dimension", NS) is not None else None)
                seen = set()
                for cell in document.iter(f"{{{MAIN}}}c"):
                    if artifact["cells_read"] >= limits.max_cells:
                        raise DocumentError("xlsx_cell_limit", "Stored cell count exceeds configured limit")
                    coordinate = _coordinate(cell.get("r", ""))
                    if coordinate in seen:
                        raise DocumentError("xlsx_duplicate_cell", "Worksheet contains duplicate cell address")
                    seen.add(coordinate)
                    cell_type = cell.get("t", "n")
                    if cell_type not in {"n", "s", "inlineStr", "str", "b", "e", "d"}:
                        raise DocumentError("xlsx_cell_type_unsupported", "Unsupported XLSX cell type")
                    raw = cell.findtext("s:v", default=None, namespaces=NS)
                    formula = cell.find("s:f", NS)
                    value = raw
                    if cell_type == "s":
                        try:
                            string_index = int(raw)
                            if string_index < 0:
                                raise IndexError
                            value = strings[string_index]
                        except (TypeError, ValueError, IndexError) as exc:
                            raise DocumentError("xlsx_invalid_shared_string", "Invalid shared string reference") from exc
                    elif cell_type == "inlineStr":
                        value = _text(cell.find("s:is", NS))
                    formula_text = (formula.text or "") if formula is not None else None
                    # All exported content shares a budget. Presence/truncation are explicit.
                    def bounded(text):
                        nonlocal remaining
                        if text is None:
                            return None, False
                        take = min(limits.max_text_chars, remaining)
                        remaining -= min(take, len(text))
                        return text[:take], len(text) > take
                    value, value_truncated = bounded(value)
                    formula_text, formula_truncated = bounded(formula_text)
                    entry = {"cell": coordinate, "value_type": cell_type,
                             "value": value if formula is None else None,
                             "formula": ({"text": formula_text,
                                          "type": formula.get("t", "normal"),
                                          "shared_index": formula.get("si"),
                                          "range": formula.get("ref")} if formula is not None else None),
                             "cached_value": ({"value": value, "value_type": cell_type,
                                               "present": raw is not None} if formula is not None else None),
                             "style_index": cell.get("s"),
                             "text_truncated": value_truncated or formula_truncated,
                             "provenance": {"source_sha256": artifact["source"]["sha256"],
                                            "sheet": index + 1, "sheet_name": row["name"],
                                            "cell": coordinate},
                             "numeric_facts": "unverified", "use_for_calculation": False}
                    row["cells"].append(entry)
                    artifact["cells_read"] += 1
                    artifact["text_truncated"] |= entry["text_truncated"]
                    if artifact["cells_read"] % 1000 == 0:
                        checkpoint(artifact)
                row.update(status="inspected", cells_complete=True)
            except DocumentError as exc:
                row.update(status="unsupported" if "unsupported" in exc.code or exc.code == "xlsx_external_sheet"
                           else "incomplete", error=exc.code)
                add_error(artifact, exc.code, str(exc))
                artifact["errors"][-1]["sheet"] = index + 1
            artifact["uninspected_sheet_count"] = len(elements) - index - 1
            checkpoint(artifact)
        artifact["inventory_complete"] = True
        artifact["cells_complete"] = all(row["cells_complete"] for row in artifact["sheets"])
    artifact["complete"] = not artifact["errors"]
    artifact["status"] = "complete" if artifact["complete"] else "incomplete"
    return artifact
