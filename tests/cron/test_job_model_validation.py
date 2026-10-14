"""K21-342: unusable agent schedules are rejected before they reach storage."""

import argparse
import json
from unittest.mock import patch

import pytest

from cron import jobs
from korra_cli.cron import cron_command
from korra_cli.subcommands.cron import build_cron_parser
from tools.cronjob_tools import cronjob


@pytest.fixture(autouse=True)
def empty_model_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    monkeypatch.delenv("KORRA_MODEL", raising=False)
    with jobs.use_cron_store(tmp_path):
        yield tmp_path


@pytest.mark.parametrize("config", [None, "{}", "model: {}", "model:\n  default: ''\n", "model:\n  default: '  '\n"])
def test_create_without_model_leaves_store_untouched(empty_model_home, config):
    if config is not None:
        (empty_model_home / "config.yaml").write_text(config)
    with pytest.raises(ValueError, match="no model configured") as caught:
        jobs.create_job(prompt="Daily report", schedule="every 1d")
    assert "config.yaml" in str(caught.value)
    assert "--model" in str(caught.value)
    assert jobs.load_jobs() == []


@pytest.mark.parametrize("config, kwargs", [
    ("", {"model": "job-model"}),
    ("model: chat-model\n", {}),
    ("model:\n  default: chat-model\n", {}),
    ("model:\n  model: chat-model\n", {}),
    ("model:\n  name: chat-model\n", {}),
    ("cron:\n  model: cron-model\n", {}),
])
def test_create_with_resolvable_model(empty_model_home, config, kwargs):
    (empty_model_home / "config.yaml").write_text(config)
    job = jobs.create_job(prompt="Daily report", schedule="every 1d", **kwargs)
    assert jobs.get_job(job["id"])["prompt"] == "Daily report"
    if not kwargs:
        assert job["model_snapshot"] == ("cron-model" if "cron:" in config else "chat-model")


def test_legacy_env_fallback_is_accepted(empty_model_home, monkeypatch):
    monkeypatch.setenv("KORRA_MODEL", "env-model")
    job = jobs.create_job(prompt="Report", schedule="every 1d")
    assert jobs.get_job(job["id"]) is not None


@pytest.mark.parametrize("updates", [{"model": None}, {"model": ""}, {"model": "   "}])
def test_clearing_only_model_does_not_modify_store(empty_model_home, updates):
    job = jobs.create_job(prompt="Report", schedule="every 1d", model="pinned-model")
    path = empty_model_home / "cron" / "jobs.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="no model configured"):
        jobs.update_job(job["id"], updates)
    assert path.read_bytes() == before


@pytest.mark.parametrize("updates", [{"name": "Renamed"}, {"schedule": "every 2d"}, {"prompt": "New report"}])
def test_edit_legacy_missing_model_rejects_merged_record(empty_model_home, updates):
    job = jobs.create_job(prompt="Report", schedule="every 1d", model="pinned-model")
    job["model"] = None
    jobs.save_jobs([job])  # Simulate a pre-existing record from 0.21.18.
    with pytest.raises(ValueError, match="no model configured"):
        jobs.update_job(job["id"], updates)
    assert jobs.get_job(job["id"]) == job
    # Stopping a broken job and recording delivery diagnostics remain possible.
    assert jobs.pause_job(job["id"])["enabled"] is False
    assert jobs.update_job(job["id"], {"last_delivery_error": "offline"})["last_delivery_error"] == "offline"
    with pytest.raises(ValueError, match="no model configured"):
        jobs.resume_job(job["id"])
    assert jobs.update_job(job["id"], {"model": "fixed-model"})["model"] == "fixed-model"
    assert jobs.resume_job(job["id"])["enabled"] is True


@pytest.mark.parametrize("cleared", [None, "", "   "])
def test_clearing_pin_follows_config_without_pinning_it(empty_model_home, cleared):
    job = jobs.create_job(prompt="Report", schedule="every 1d", model="pinned-model")
    (empty_model_home / "config.yaml").write_text("cron:\n  model: fleet-model\n")
    edited = jobs.update_job(job["id"], {"model": cleared})
    assert edited["model"] is None
    assert edited["model_snapshot"] == "fleet-model"


def test_script_only_and_reminder_jobs_need_no_model(empty_model_home):
    script = jobs.create_job(prompt="", script="report.py", no_agent=True, schedule="every 1d")
    reminder = jobs.create_job(prompt="", reminder="Позвонить", schedule="in 1h")
    assert jobs.update_job(reminder["id"], {"reminder": "Написать"})["reminder"] == "Написать"
    with pytest.raises(ValueError, match="no model configured"):
        jobs.update_job(script["id"], {"no_agent": False})
    assert jobs.get_job(script["id"])["no_agent"] is True


def test_tool_and_cli_report_missing_model_without_saving(empty_model_home, capsys):
    tool_result = json.loads(cronjob(action="create", prompt="Report", schedule="every 1d"))
    assert tool_result["success"] is False
    assert "no model configured" in tool_result["error"]
    parser = argparse.ArgumentParser()
    build_cron_parser(parser.add_subparsers(dest="command"), cmd_cron=cron_command)
    result = cron_command(parser.parse_args(["cron", "add", "every 1d", "Report"]))
    assert result == 1
    assert "не настроена модель" in capsys.readouterr().out
    assert jobs.load_jobs() == []


@pytest.mark.parametrize("deliver", ["local", "telegram:123"])
def test_existing_failure_incident_and_owner_alert_cover_legacy_job(empty_model_home, monkeypatch, capsys, deliver):
    from cron import incidents, scheduler

    job = jobs.create_job(prompt="Report", name="Legacy report", schedule="every 1d", model="pinned-model", deliver=deliver)
    job["model"] = None
    jobs.save_jobs([job])
    monkeypatch.setattr(scheduler, "_hermes_home", empty_model_home)
    notices = []
    with patch.object(scheduler, "_deliver_result", side_effect=lambda _job, text, **_kw: notices.append(text)), \
         patch("run_agent.AIAgent", side_effect=AssertionError("a missing-model job must not create an agent")):
        for _ in range(3):
            assert scheduler.run_one_job(jobs.get_job(job["id"])) is True
    stored = jobs.get_job(job["id"])
    assert stored["failure_streak"] == 3
    assert "no model configured" in stored["last_error"]
    rows = incidents.list_incidents()
    assert len(rows) == 1
    assert rows[0]["job_id"] == job["id"]
    if deliver == "local":
        assert rows[0]["state"] == "detected"  # No implied permission to send to a human.
        assert "3 runs in a row" in notices[-1]
    else:
        assert rows[0]["state"] == "alerted"
        assert len(notices) == 1  # Existing 6h dedup, no per-tick spam.
    assert "no model" in notices[0].lower()
    cron_command(argparse.Namespace(cron_command="list", all=True))
    assert "ошибок подряд: 3" in capsys.readouterr().out
