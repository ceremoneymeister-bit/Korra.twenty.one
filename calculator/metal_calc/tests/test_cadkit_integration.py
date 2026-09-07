from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from metal_calc.geometry import CadkitAdapter


def test_real_cadkit_rectangle(tmp_path: Path) -> None:
    drawing = ezdxf.new("R2013")
    drawing.header["$INSUNITS"] = 4
    drawing.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 50), (0, 50)], close=True
    )
    source = tmp_path / "rectangle.dxf"
    drawing.saveas(source)
    workdir = tmp_path / "work"
    workdir.mkdir()
    adapter = CadkitAdapter(
        Path(__file__).resolve().parents[2] / "lib" / "cadkit.py", timeout_seconds=30
    )
    results, versions = adapter.analyze(
        [source], thickness_mm=4.0, density_kg_m3=7850.0, workdir=workdir
    )
    assert results[0]["ok"] is True
    assert results[0]["area_mm2"] == pytest.approx(5000.0, rel=1e-6)
    assert results[0]["cut_length_mm"] == pytest.approx(300.0, rel=1e-6)
    assert versions["ezdxf_version"] == "1.4.4"


def test_cadkit_worker_does_not_inherit_parent_secrets(tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "fake_cadkit.py"
    fake.write_text(
        """
import os
from dataclasses import dataclass, field
class _Ezdxf:
    __version__ = "test"
ezdxf = _Ezdxf()
@dataclass
class Part:
    file: str
    ok: bool = True
    warnings: list[str] = field(default_factory=list)
def analyze(path, thickness, density, workdir):
    return Part(file=path, warnings=[os.environ.get("METAL_CALC_SECRET_TEST", "absent")])
""",
        encoding="utf-8",
    )
    source = tmp_path / "source.dxf"
    source.write_text("synthetic", encoding="ascii")
    workdir = tmp_path / "work-safe-env"
    workdir.mkdir()
    monkeypatch.setenv("METAL_CALC_SECRET_TEST", "must-not-leak")
    adapter = CadkitAdapter(fake, timeout_seconds=10)
    results, _ = adapter.analyze(
        [source], thickness_mm=1, density_kg_m3=1, workdir=workdir
    )
    assert results[0]["warnings"] == ["absent"]
