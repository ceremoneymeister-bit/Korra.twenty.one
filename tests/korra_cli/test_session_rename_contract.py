"""Owner rename through real HTTP and two independent profile databases."""
from pathlib import Path
import pytest
from starlette.testclient import TestClient
from korra_cli import web_server as server
from korra_state import SessionDB

@pytest.fixture
def contour(tmp_path, monkeypatch):
    home = tmp_path / 'data'
    home.mkdir()
    monkeypatch.setenv('KORRA_HOME', str(home))
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    for target in (home, home/'profiles'/'designer'):
        target.mkdir(parents=True, exist_ok=True)
        with SessionDB(db_path=target/'state.db') as db:
            db.create_session('same-id', 'dashboard')
            db.set_session_title('same-id', 'Исходное имя')
            db.append_message('same-id', 'user', 'Синтетическая задача')
            db.append_message('same-id', 'assistant', 'Синтетический ответ')
            db.create_session('other-id', 'dashboard')
            db.set_session_title('other-id', 'Занятое имя')
    client=TestClient(server.app)
    client.headers[server._SESSION_HEADER_NAME]=server._SESSION_TOKEN
    yield client,home
    client.close()

def test_rename_is_scoped_and_late_auto_title_cannot_replace_it(contour):
    client,home=contour
    response=client.patch('/api/sessions/same-id',json={'profile':'designer','title':'  Договор   на русском  '})
    assert response.status_code==200,response.text
    assert response.json()['title']=='Договор на русском'
    with SessionDB(db_path=home/'profiles'/'designer'/'state.db') as db:
        assert not db.set_auto_title('same-id','Поздний заголовок',source='llm')
        assert db.get_session_title('same-id')=='Договор на русском'
        assert len(db.get_messages('same-id'))==2
        assert db.get_session_title('other-id')=='Занятое имя'
    with SessionDB(db_path=home/'state.db') as db:
        assert db.get_session_title('same-id')=='Исходное имя'

@pytest.mark.parametrize(('title','hint'),[('Занятое имя','уже есть'),('Я'*101,'не больше 100')])
def test_rejected_title_explains_how_to_continue_and_keeps_old_name(contour,title,hint):
    client,home=contour
    response=client.patch('/api/sessions/same-id',json={'profile':'designer','title':title})
    assert response.status_code==400,response.text
    assert hint in response.json()['detail']
    with SessionDB(db_path=home/'profiles'/'designer'/'state.db') as db:
        assert db.get_session_title('same-id')=='Исходное имя'

def test_empty_title_keeps_the_existing_clear_contract(contour):
    client,_=contour
    response=client.patch('/api/sessions/same-id',json={'profile':'designer','title':'  '})
    assert response.status_code==200,response.text
    assert response.json()['title']==''
