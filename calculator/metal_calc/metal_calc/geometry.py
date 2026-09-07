from __future__ import annotations

import importlib.util
import multiprocessing
import os
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .errors import GeometryFailed


def _cadkit_worker(
    cadkit_path: str,
    inputs: list[str],
    thickness: float,
    density: float,
    workdir: str,
    connection: Any,
) -> None:
    try:
        os.setsid()
        os.environ.clear()
        os.environ.update(
            {
                "PATH": "/opt/metal-calc/bin:/usr/local/bin:/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "HOME": workdir,
                "TMPDIR": workdir,
            }
        )
        spec = importlib.util.spec_from_file_location("metal_calc_packaged_cadkit", cadkit_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("cadkit cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        results = [asdict(module.analyze(path, thickness, density, workdir)) for path in inputs]
        versions = {
            "cadkit_version": "source-sha256-managed-by-image",
            "ezdxf_version": getattr(module.ezdxf, "__version__", "unknown"),
        }
        try:
            import shapely

            versions["shapely_version"] = shapely.__version__
        except ImportError:
            versions["shapely_version"] = "unknown"
        connection.send((True, results, versions))
    except BaseException as exc:  # noqa: BLE001
        connection.send((False, f"{type(exc).__name__}: {exc}", {}))
    finally:
        connection.close()


class CadkitAdapter:
    def __init__(self, cadkit_path: Path, timeout_seconds: int = 120) -> None:
        if cadkit_path.is_symlink() or not cadkit_path.is_file():
            raise RuntimeError("cadkit must be a regular immutable-image file")
        self.cadkit_path = cadkit_path
        self.timeout_seconds = timeout_seconds

    def analyze(
        self,
        inputs: list[Path],
        *,
        thickness_mm: float,
        density_kg_m3: float,
        workdir: Path,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        parent, child = multiprocessing.Pipe(duplex=False)
        process = multiprocessing.Process(
            target=_cadkit_worker,
            args=(
                str(self.cadkit_path),
                [str(path) for path in inputs],
                thickness_mm,
                density_kg_m3,
                str(workdir),
                child,
            ),
            daemon=True,
        )
        process.start()
        child.close()
        deadline = time.monotonic() + self.timeout_seconds
        message = None
        while time.monotonic() < deadline:
            if parent.poll(min(0.1, max(0.0, deadline - time.monotonic()))):
                message = parent.recv()
                break
            if not process.is_alive():
                break
        if message is None and process.is_alive():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.join(5)
            parent.close()
            raise GeometryFailed("Geometry analysis timed out")
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
        if message is None and parent.poll():
            message = parent.recv()
        if message is None:
            parent.close()
            raise GeometryFailed("Geometry analysis process failed")
        ok, payload, versions = message
        parent.close()
        if not ok:
            raise GeometryFailed("Geometry analysis failed")
        return payload, versions
