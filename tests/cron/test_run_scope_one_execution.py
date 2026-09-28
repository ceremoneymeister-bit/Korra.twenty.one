"""Одно выполнение — одна граница отправок (третье чистое ревью Astra 0.21.15).

P1-1: скрипт и ход агента одного выполнения не отправляют одно и то же
дважды, даже со своими секретами и после неопределённого исхода. P1-2:
отправка из запуска подчиняется тому же договору адресатов, что автодоставка
результата. Синтетические данные, транспорт перехвачен, внешних отправок нет.
"""
import json
from unittest.mock import MagicMock
import pytest
from cron import recipients

@pytest.fixture
def run(tmp_path, monkeypatch):
    from cron import jobs, executions
    from gateway.config import Platform
    monkeypatch.setenv('KORRA_HOME',str(tmp_path))
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    monkeypatch.setattr('gateway.config.load_gateway_config',lambda:MagicMock(platforms={Platform.TELEGRAM:MagicMock(enabled=True,token='synthetic')}))
    sent=[]
    def dispatch(**kw):
        sent.append((kw['chat_id'],kw['cleaned_message']))
        return {'success':True,'message_id':str(len(sent))}
    monkeypatch.setattr('tools.send_message_tool._dispatch_resolved_send',dispatch)
    with jobs.use_cron_store(tmp_path):
        job=jobs.create_job(prompt='Birthday',schedule='every 1h',deliver='local',created_by_owner=True,recipients_policy=1,recipients_confirmed={'targets':['telegram:555']})
        execution=executions.create_execution(job['id'],source='scheduled')
        executions.mark_execution_running(execution['id'])
        job['execution_id']=execution['id']
        yield job,sent
        recipients._LIVE_RUNS.clear(); recipients._RUN_SENDS.clear()
        executions.finish_execution(execution['id'],success=True)

@pytest.mark.parametrize("first_unknown", [False, True])
def test_pre_script_then_agent_same_execution_should_not_duplicate(run, monkeypatch, first_unknown):
    from cron.scheduler import _job_send_context
    from gateway.session_context import get_session_env
    job,sent=run
    if first_unknown:
        def transport(**kw):
            sent.append((kw['chat_id'],kw['cleaned_message']))
            if len(sent)==1: raise TimeoutError('accepted; acknowledgement lost')
            return {'success':True}
        monkeypatch.setattr('tools.send_message_tool._dispatch_resolved_send',transport)
    with _job_send_context(job,job['execution_id']):
        secret=get_session_env(recipients.RUNNING_JOB_ENV,'')
        grant=recipients.lookup_live_run(secret)
        first=recipients.send_once_for_live_run(secret,grant,'telegram:555','Birthday')
        assert first.get('status')=='outcome_unknown' if first_unknown else first['success']
    # Следующая стадия того же выполнения со своим секретом (агент после скрипта).
    secret=recipients.issue_run_token(job)
    try:
        grant=recipients.lookup_live_run(secret)
        again=recipients.send_once_for_live_run(secret,grant,'telegram:555','Birthday')
        assert again.get('repeat') is True
        assert (again.get('status')=='outcome_unknown') if first_unknown else again['success']
    finally:
        recipients.retire_run_token(secret)
    assert len(sent)==1, f'One execution delivered {len(sent)} copies: {sent}'


def test_the_send_record_goes_when_the_execution_ends(run):
    from cron import executions
    job,sent=run
    secret=recipients.issue_run_token(job)
    grant=recipients.lookup_live_run(secret)
    assert recipients.send_once_for_live_run(secret,grant,'telegram:555','Birthday')['success']
    recipients.retire_run_token(secret)
    assert recipients._RUN_SENDS.get(job['execution_id'])
    executions.finish_execution(job['execution_id'],success=True)
    recipients.retire_run_token('other-secret')
    assert job['execution_id'] not in recipients._RUN_SENDS

def test_employee_run_can_send_to_its_own_private_chat(run):
    from cron import jobs
    from cron.scheduler import _is_creator_private_chat
    job,sent=run
    job=jobs.update_job(job['id'],{'created_by_owner':False,'origin':{'platform':'telegram','chat_id':'1220','user_id':'1220','chat_type':'dm','owner':False}})
    assert _is_creator_private_chat(job,'telegram','1220')
    # Reuse the real running execution; update_job returns persisted job metadata.
    from cron.executions import _transaction
    with _transaction() as conn:
        execution=conn.execute("SELECT id FROM executions WHERE job_id=? AND status='running'",(job['id'],)).fetchone()
    secret=recipients.issue_run_token({**job,'execution_id':execution['id']})
    try:
        grant=recipients.lookup_live_run(secret)
        result=recipients.send_once_for_live_run(secret,grant,'telegram:1220','My own reminder')
        assert result.get('success'), result
    finally: recipients.retire_run_token(secret)

def test_owner_form_recipient_is_confirmed_for_run_send(tmp_path, monkeypatch):
    from cron import jobs, executions
    from gateway.config import Platform
    monkeypatch.setenv('KORRA_HOME',str(tmp_path)); monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    monkeypatch.setattr('gateway.config.load_gateway_config',lambda:MagicMock(platforms={Platform.TELEGRAM:MagicMock(enabled=True,token='synthetic')},get_home_channel=lambda _:None))
    monkeypatch.setattr('tools.send_message_tool._dispatch_resolved_send',lambda **kw:{'success':True})
    with jobs.use_cron_store(tmp_path):
        # Same create_job path as the cabinet form: chosen deliver is confirmation.
        job=jobs.create_job(prompt='',schedule='every 1h',deliver='telegram:555',script='report.py',no_agent=True,created_by_owner=True)
        assert recipients.delivery_allowed(job,'telegram','555')
        execution=executions.create_execution(job['id'],source='scheduled'); executions.mark_execution_running(execution['id'])
        secret=recipients.issue_run_token({**job,'execution_id':execution['id']})
        try:
            grant=recipients.lookup_live_run(secret)
            result=recipients.send_once_for_live_run(secret,grant,'telegram:555','Approved report')
            assert result.get('success'),result
        finally:
            recipients.retire_run_token(secret); executions.finish_execution(execution['id'],success=True)


def test_a_form_job_may_not_send_beyond_its_chosen_target(tmp_path, monkeypatch):
    """Выбор в форме разрешает ровно выбранную цель, а не любой адрес."""
    from cron import jobs, executions
    from gateway.config import Platform
    monkeypatch.setenv('KORRA_HOME',str(tmp_path)); monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    monkeypatch.setattr('gateway.config.load_gateway_config',lambda:MagicMock(platforms={Platform.TELEGRAM:MagicMock(enabled=True,token='synthetic')},get_home_channel=lambda _:None))
    sent=[]
    monkeypatch.setattr('tools.send_message_tool._dispatch_resolved_send',lambda **kw:sent.append(kw['chat_id']) or {'success':True})
    with jobs.use_cron_store(tmp_path):
        job=jobs.create_job(prompt='',schedule='every 1h',deliver='telegram:555',script='report.py',no_agent=True,created_by_owner=True)
        execution=executions.create_execution(job['id'],source='scheduled'); executions.mark_execution_running(execution['id'])
        secret=recipients.issue_run_token({**job,'execution_id':execution['id']})
        try:
            grant=recipients.lookup_live_run(secret)
            result=recipients.send_once_for_live_run(secret,grant,'telegram:777','Someone else')
            assert not result.get('success')
            assert sent==[]
        finally:
            recipients.retire_run_token(secret); executions.finish_execution(execution['id'],success=True)


def test_an_employee_run_may_not_message_other_people(run):
    from cron import jobs
    from cron.executions import _transaction
    job,sent=run
    job=jobs.update_job(job['id'],{'created_by_owner':False,'origin':{'platform':'telegram','chat_id':'1220','user_id':'1220','chat_type':'dm','owner':False}})
    with _transaction() as conn:
        execution=conn.execute("SELECT id FROM executions WHERE job_id=? AND status='running'",(job['id'],)).fetchone()
    secret=recipients.issue_run_token({**job,'execution_id':execution['id']})
    try:
        grant=recipients.lookup_live_run(secret)
        result=recipients.send_once_for_live_run(secret,grant,'telegram:555','To a client')
        assert not result.get('success')
        assert sent==[]
    finally: recipients.retire_run_token(secret)
