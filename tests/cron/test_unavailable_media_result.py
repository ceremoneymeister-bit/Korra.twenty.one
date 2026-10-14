"""T4-04/K21-345: explicit unavailable files reach the real delivery ledger."""
import pytest

from cron import executions, incidents, jobs, scheduler
from gateway.platforms.base import MEDIA_DELIVERY_FAILURE_NOTICE
from korra_state import SessionDB
from tests.cron.test_delivery_attention import store, _script_job  # noqa: F401


@pytest.mark.parametrize("tag", [
    "MEDIA:{home}/missing.rbz", "MEDIA:relative/report.pdf",
    'MEDIA:"relative/Отчёт с пробелами.rbz"',
    "MEDIA:{home}/Отчёт с пробелами.rbz", "MEDIA:{home}/Caddyfile",
])
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("with_valid", [False, True])
def test_unavailable_explicit_file_records_failure(store, tag, required, with_valid):
    with SessionDB() as db:
        db.create_session(session_id="web-chat", source="api_server")
    content = tag.format(home=store)
    if with_valid:
        valid = store / "Доступный файл.rbz"
        valid.write_text("report")
        content += f'\nMEDIA:"{valid}"'
    job = _script_job(store, content=content, required_file=required)
    jobs.update_job(job["id"], {"deliver": "api_server:web-chat"})
    assert scheduler.run_one_job(jobs.get_job(job["id"]))
    saved = jobs.get_job(job["id"])
    run = executions.latest_execution(job["id"])
    assert saved["last_status"] == ("error" if required and not with_valid else "delivery_failed")
    assert run["delivery_outcome"] == "failed"
    assert incidents.list_incidents()
    with SessionDB() as db:
        messages = db.get_messages("web-chat")
    if not required or with_valid:
        assert any(MEDIA_DELIVERY_FAILURE_NOTICE in (m.get("content") or "") for m in messages)
        assert all("MEDIA:" not in (m.get("content") or "") for m in messages)
    assert all(tag.format(home=store) not in (m.get("content") or "") for m in messages)
