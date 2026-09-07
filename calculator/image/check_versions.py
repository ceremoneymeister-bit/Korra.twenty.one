#!/usr/bin/env python3
"""Fail a composite build if the calculation venv drifts."""

from importlib.metadata import version

from openpyxl.xml import LXML


EXPECTED = {
    "ezdxf": "1.4.4",
    "shapely": "2.1.2",
    "numpy": "2.5.2",
    "fonttools": "4.63.0",
    "mcp": "1.28.1",
    "openpyxl": "3.1.5",
    "jsonschema": "4.26.0",
    "lxml": "6.1.1",
}

actual = {name: version(name) for name in EXPECTED}
if actual != EXPECTED:
    raise SystemExit(f"metal-calc dependency mismatch: {actual!r}")
if not LXML:
    raise SystemExit("metal-calc XLSX serializer mismatch: openpyxl must use lxml")
