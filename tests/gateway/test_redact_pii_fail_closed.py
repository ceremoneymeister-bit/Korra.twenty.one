"""QA 17-18.10: a failed config read must not silently switch PII redaction
off for the turn; fall back to the safe mode (redact) with a warning."""

import logging

import gateway.run as gateway_run
from korra_cli.config import FailedConfigRead


def test_explicit_setting_is_honored(monkeypatch):
    monkeypatch.setattr(gateway_run, "_load_gateway_config",
                        lambda: {"privacy": {"redact_pii": True}})
    assert gateway_run._gateway_redact_pii_setting() is True
    monkeypatch.setattr(gateway_run, "_load_gateway_config",
                        lambda: {"privacy": {"redact_pii": False}})
    assert gateway_run._gateway_redact_pii_setting() is False
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    assert gateway_run._gateway_redact_pii_setting() is False


def test_loader_exception_fails_closed(monkeypatch, caplog):
    def boom():
        raise OSError("disk gone")

    monkeypatch.setattr(gateway_run, "_load_gateway_config", boom)
    with caplog.at_level(logging.WARNING, logger="gateway.run"):
        assert gateway_run._gateway_redact_pii_setting() is True
    assert any("redact_pii" in r.getMessage() for r in caplog.records)


def test_malformed_privacy_section_fails_closed(monkeypatch):
    monkeypatch.setattr(gateway_run, "_load_gateway_config",
                        lambda: {"privacy": "yes please"})
    assert gateway_run._gateway_redact_pii_setting() is True


def test_failed_read_stand_in_fails_closed(monkeypatch):
    monkeypatch.setattr(gateway_run, "_load_gateway_config",
                        lambda: FailedConfigRead(error=OSError("EIO")))
    assert gateway_run._gateway_redact_pii_setting() is True


def test_last_known_good_from_failed_read_is_used(monkeypatch):
    monkeypatch.setattr(
        gateway_run, "_load_gateway_config",
        lambda: FailedConfigRead({"privacy": {"redact_pii": False}}, error=OSError("EIO")),
    )
    assert gateway_run._gateway_redact_pii_setting() is False


def test_unreadable_config_file_fails_closed(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text("privacy: {redact_pii: [unclosed\n")
    monkeypatch.setattr(gateway_run, "_gateway_config_home", lambda: tmp_path)
    assert gateway_run._gateway_redact_pii_setting() is True
