"""Credential values survive persistence, startup and profile reads literally."""
import os

import pytest

from agent.secret_scope import load_env_file
from korra_cli.config import save_env_value
from korra_cli.env_loader import _load_dotenv_with_fallback


@pytest.mark.parametrize("value", [
    "${DEFINED}", "${MISSING}", "${DEFINED:-fallback}",
    'literal ${DEFINED} # "quotes" \\ path', "${DEFINED} 'single' $dollar",
    "пароль ${DEFINED}",
])
def test_writer_startup_and_scope_are_literal(tmp_path, monkeypatch, value):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("DEFINED", "must-not-expand")
    monkeypatch.setenv("LITERAL_PASSWORD", "old")
    save_env_value("LITERAL_PASSWORD", value)
    _load_dotenv_with_fallback(tmp_path / ".env", override=True)
    assert os.environ["LITERAL_PASSWORD"] == value
    assert load_env_file(tmp_path / ".env")["LITERAL_PASSWORD"] == value


@pytest.mark.parametrize("encoding,bom", [("utf-8", b""), ("utf-8", b"\xef\xbb\xbf"),
                                         ("latin-1", b""), ("latin-1", b"\xef\xbb\xbf")])
def test_encodings_and_process_precedence(tmp_path, monkeypatch, encoding, bom):
    path = tmp_path / ".env"
    path.write_bytes(bom + "export LITERAL_PASSWORD='café ${DEFINED}' # comment\r\n".encode(encoding))
    monkeypatch.setenv("DEFINED", "must-not-expand")
    monkeypatch.setenv("LITERAL_PASSWORD", "process")
    _load_dotenv_with_fallback(path, override=False)
    assert os.environ["LITERAL_PASSWORD"] == "process"
    _load_dotenv_with_fallback(path, override=True)
    assert os.environ["LITERAL_PASSWORD"] == "café ${DEFINED}"
    assert load_env_file(path)["LITERAL_PASSWORD"] == "café ${DEFINED}"
