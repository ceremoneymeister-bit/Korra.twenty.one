"""Locate the separately packaged document reader, without parser imports."""
import importlib.util
from pathlib import Path


def load_reader():
    local = Path(__file__).resolve().parents[2] / "documents" / "bridge.py"
    installed = Path("/opt/metal-calc/documents/bridge.py")
    path = local if local.is_file() else installed
    spec = importlib.util.spec_from_file_location("calc21_document_bridge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Document reader is not installed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
