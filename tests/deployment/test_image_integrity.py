"""Host Docker identity is compared to publishing evidence, not labels."""
import json

import pytest

from test_client_updater import u, updater, OLD, NEW


CUSTOM = "sha256:" + "a" * 64


@pytest.fixture
def integrity(updater, monkeypatch):
    monkeypatch.setattr(u, "trusted_control", lambda path: None)
    updater.image_integrity = u.Updater.image_integrity.__get__(updater)
    registry = {"schema": 1, "active_release": "current", "releases": {
        "previous": {"status": "accepted", "artifact": {"image": "ghcr.io/ceremoneymeister-bit/korra.twenty.one@" + OLD, "image_id": OLD}},
        "current": {"status": "accepted", "artifact": {"image": "ghcr.io/ceremoneymeister-bit/korra.twenty.one@" + NEW, "image_id": NEW}},
    }}
    (updater.home / "release-registry.json").write_text(json.dumps(registry))
    return updater


def test_official_previous_is_accepted_even_when_stable_moved(integrity):
    report = integrity.image_integrity()
    assert report["origin"] == "official" and report["release"] == "previous"
    assert report["stable_release"] == "current"
    assert report["diff_available"] and report["changed_files"] == []


def test_inherited_label_does_not_make_derivative_official(integrity):
    report = integrity.image_integrity({"Image": CUSTOM, "Config": {"Labels": {"org.opencontainers.image.revision": "same"}}})
    assert report["origin"] == "custom"


@pytest.mark.parametrize("kind", ["A", "C", "D"])
def test_diff_shows_new_changed_and_deleted_platform_files(integrity, kind):
    integrity.diff_output = f"{kind} /opt/hermes/cron/scheduler.py\nC /opt/data/config.yaml\nC /opt/hermes"
    report = integrity.image_integrity()
    assert report["changed_files"] == [{"change": kind, "path": "/opt/hermes/cron/scheduler.py"}]
    assert report["changed_count"] == 1


def test_code_mount_is_reported_separately(integrity):
    integrity.mounts_override = [{"Destination": "/opt/hermes/cron"}]
    report = integrity.image_integrity()
    assert report["code_mounts"] == ["/opt/hermes/cron"]


@pytest.mark.parametrize("content", [None, "broken", '{"schema":1,"releases":{}}'])
def test_missing_or_invalid_registry_is_unknown_not_official(integrity, content):
    path = integrity.home / "release-registry.json"
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    assert integrity.image_integrity()["origin"] == "unknown"
    with pytest.raises(u.UpdateError, match="accept-custom-current"):
        integrity.check_current_image({"Image": OLD})


def test_diff_failure_is_visible(integrity, monkeypatch):
    monkeypatch.setattr(integrity, "docker", lambda *x, **kw: (_ for _ in ()).throw(u.UpdateError("diff failed")))
    report = integrity.image_integrity({"Image": OLD})
    assert not report["diff_available"] and report["diff_error"]


def test_custom_refused_before_pull_or_stop_and_dry_run_reports(integrity):
    integrity.image = CUSTOM
    integrity.tags[CUSTOM] = CUSTOM
    (integrity.home / "IMAGE").write_text(CUSTOM)
    with pytest.raises(u.UpdateError, match="accept-custom-current"):
        integrity.update("official-target")
    assert not any(call[0] in {"stop", "pull", "probe", "start_image"} for call in integrity.calls)
    assert integrity.receipt["error_code"] == "custom_current_confirmation_required"
    integrity.update("official-target", dry_run=True)
    assert integrity.receipt["image_integrity"]["origin"] == "custom"
    assert integrity.receipt["phase"] == "dry_run"


def test_exact_confirmation_allows_official_return_and_custom_rollback(integrity):
    integrity.image = CUSTOM
    integrity.tags[CUSTOM] = CUSTOM
    (integrity.home / "IMAGE").write_text(CUSTOM)
    integrity.receipt["accept_custom_current"] = OLD
    with pytest.raises(u.UpdateError):
        integrity.update("official-target")
    integrity.receipt["accept_custom_current"] = CUSTOM
    integrity.update("official-target")
    assert integrity.image == NEW
    integrity.rollback()
    assert integrity.image == CUSTOM and integrity.receipt["status"] == "rolled_back"


def test_confirmation_cli_is_bound_to_full_current_id():
    assert u.parse_args(["--update", "stable", "--accept-custom-current", CUSTOM]).accept_custom_current == CUSTOM
    for args in (["--update", "stable", "--accept-custom-current", "custom"],
                 ["--rollback", "job", "--accept-custom-current", CUSTOM]):
        with pytest.raises(SystemExit):
            u.parse_args(args)


def test_doctor_uses_host_report_and_displays_custom_and_drift(monkeypatch, capsys):
    import subprocess
    from korra_cli.doctor import _check_deployment_image
    report = {"origin": "custom", "image_id": CUSTOM, "diff_available": True, "changed_count": 1,
              "changed_files": [{"change": "C", "path": "/opt/hermes/run_agent.py"}]}
    monkeypatch.setattr("korra_cli.doctor.subprocess.run", lambda *x, **kw: subprocess.CompletedProcess([], 0, json.dumps(report), ""))
    _check_deployment_image("/synthetic/deploy")
    output = capsys.readouterr().out
    assert "Нестандартный образ" in output and CUSTOM in output and "/opt/hermes/run_agent.py" in output
