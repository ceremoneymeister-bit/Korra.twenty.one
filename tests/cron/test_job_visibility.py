"""Filter private jobs before formatting or resolving names (K21-242)."""
import json

import pytest

from cron.jobs import create_job
from gateway.session_context import clear_session_vars, set_session_vars
from tools.cronjob_tools import cronjob


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron/jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron/output")
    owner = create_job(prompt="private owner secret", schedule="every 1h", name="Same", created_by_owner=True)
    visitor = create_job(prompt="my report", schedule="every 1h", name="Same", created_by_owner=False,
                         origin={"platform": "telegram", "chat_id": "777", "user_id": "777", "owner": False})
    return owner, visitor


def call(session, **args):
    tokens = set_session_vars(cron_session="", owner_principal="", **session)
    try:
        return json.loads(cronjob(**args))
    finally:
        clear_session_vars(tokens)


VISITOR = {"platform": "telegram", "chat_type": "dm", "chat_id": "777", "user_id": "777"}


def test_visitor_sees_only_own_job_and_name_is_not_ambiguous(jobs):
    owner, visitor = jobs
    result = call(VISITOR, action="list", include_disabled=True)
    assert result["count"] == 1 and result["jobs"][0]["job_id"] == visitor["id"]
    assert "private owner secret" not in json.dumps(result)
    assert call(VISITOR, action="pause", job_id="Same")["success"]
    hidden = call(VISITOR, action="pause", job_id=owner["id"])
    assert hidden["success"] is False and "not found" in hidden["error"]
    assert "matches" not in hidden


@pytest.mark.parametrize("session", [
    {**VISITOR, "user_id": "888", "chat_id": "888"},
    {**VISITOR, "platform": "discord"},
    {**VISITOR, "chat_type": "group"},
    {"platform": "webhook"},
])
def test_no_cross_user_platform_or_group_visibility(jobs, session):
    assert call(session, action="list", include_disabled=True)["jobs"] == []


def test_owner_sees_both_jobs(jobs):
    assert call({"platform": "cli"}, action="list", include_disabled=True)["count"] == 2
