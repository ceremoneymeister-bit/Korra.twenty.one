"""Quiet agent work survives the old proxy deadline; reattachment never resends."""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from korra_cli import web_server
from korra_cli.chat_delivery import openai_json_to_sse
from korra_cli.chat_runs import chat_runs, resume_chat_run, cancel_chat_run


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('KORRA_HOME', str(tmp_path))
    monkeypatch.setenv('API_SERVER_KEY', 'isolated-long-chat-only')
    monkeypatch.setattr(web_server, '_CHAT_DELIVERY_TASKS', {})
    monkeypatch.setattr(web_server, '_CHAT_DELIVERY_STREAMS', {})
    return web_server._chat_delivery_ledger()


@pytest.mark.parametrize('durable,stream', [(False,False),(False,True),(True,False),(True,True)])
def test_quiet_tcp_upstream_outlives_old_read_deadline(isolated, monkeypatch, durable, stream):
    # Preserve a real HTTP socket + real timeout handling, compress only the
    # historical 120-second deadline to 50 ms to keep regression runs cheap.
    real_timeout = httpx.Timeout
    monkeypatch.setattr(httpx, 'Timeout', lambda value=httpx.USE_CLIENT_DEFAULT, **kwargs:
                        real_timeout(0.05 if value == 120.0 else value, **kwargs))
    async def scenario():
        calls = []
        async def upstream(reader, writer):
            header = await reader.readuntil(b'\r\n\r\n')
            length = next(int(line.split(b':',1)[1]) for line in header.split(b'\r\n') if line.lower().startswith(b'content-length:'))
            data = json.loads(await reader.readexactly(length));calls.append(data)
            await asyncio.sleep(0.12)
            payload = json.dumps({'choices':[{'message':{'role':'assistant','content':'Долгая работа завершена'},'finish_reason':'stop'}]}, ensure_ascii=False).encode()
            if data['stream']: payload = openai_json_to_sse(payload)
            kind = b'text/event-stream' if data['stream'] else b'application/json'
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: '+kind+b'\r\nContent-Length: '+str(len(payload)).encode()+b'\r\nConnection: close\r\n\r\n'+payload)
            await writer.drain();writer.close();await writer.wait_closed()
        server = await asyncio.start_server(upstream, '127.0.0.1', 0)
        monkeypatch.setattr(web_server, '_API_SERVER_PROXY_TARGET', f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}')
        app = FastAPI()
        app.router.routes.extend(r for r in web_server.app.router.routes if getattr(r,'path',None)=='/api/chat/completions')
        headers = {'X-Hermes-Session-Id':'long-session'}
        if durable: headers['X-Korra-Client-Message-Id']='long-message-1234567890'
        try:
            async with server, httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                body = {'messages':[{'role':'user','content':'Одна долгая работа'}], 'stream':stream}
                response = await client.post('/api/chat/completions',headers=headers,json=body)
                assert response.status_code == 200, response.text
                assert 'Долгая работа завершена' in response.text
                if durable:
                    replay = await client.post('/api/chat/completions',headers=headers,json=body)
                    assert replay.content == response.content
                    assert replay.headers['X-Korra-Delivery-State'] == 'replayed'
                assert len(calls) == 1
        finally:
            server.close();await server.wait_closed()
    asyncio.run(scenario())


def remember(ledger):
    mid='long-message-1234567890'
    ledger.claim(mid,mid,'long-session',web_server._CHAT_DELIVERY_BOOT_ID)
    ledger.remember_request(mid,'researcher',{'messages':[{'role':'user','content':'Долгая задача'}]})
    return mid


def test_completed_json_run_is_replayed_as_browser_sse(isolated):
    async def scenario():
        mid=remember(isolated)
        raw=json.dumps({'choices':[{'message':{'role':'assistant','content':'Результат'},'finish_reason':'stop'}]}).encode()
        isolated.complete(mid,response_body=raw,status_code=200,content_type='application/json')
        response=await resume_chat_run(mid,'long-session','researcher')
        assert response.media_type == 'text/event-stream'
        assert b'data: [DONE]' in response.body
        assert json.loads(response.body.splitlines()[0][6:])['choices'][0]['delta']['content']=='Результат'
    asyncio.run(scenario())


def test_pending_nonstream_run_remains_running_and_can_be_reattached(isolated):
    async def scenario():
        mid=remember(isolated);gate=asyncio.Event()
        raw=b'{"choices":[{"message":{"role":"assistant","content":"ready"},"finish_reason":"stop"}]}'
        async def finish():
            await gate.wait()
            isolated.complete(mid,response_body=raw,status_code=200,content_type='application/json')
            return 200,raw,'application/json'
        task=asyncio.create_task(finish());web_server._CHAT_DELIVERY_TASKS[f'{isolated.path}:{mid}']=task
        try:
            assert (await chat_runs('researcher','long-session'))['runs'][0]['status']=='running'
            with pytest.raises(HTTPException) as exc:
                await cancel_chat_run(mid,'long-session','researcher')
            assert exc.value.status_code==409  # This transport cannot confirm upstream interruption.
            first=await resume_chat_run(mid,'long-session','researcher')
            assert first.media_type=='text/event-stream'
            reader=first.body_iterator
            await anext(reader)  # Immediate keepalive opens the response.
            await reader.aclose()
            assert not task.done()
            second=await resume_chat_run(mid,'long-session','researcher')
            gate.set();chunks=[chunk async for chunk in second.body_iterator]
            assert b'"ready"' in b''.join(chunks) and b'data: [DONE]' in b''.join(chunks)
            assert (await chat_runs('researcher','long-session'))['runs'][0]['status']=='completed'
        finally:
            gate.set();await task
    asyncio.run(scenario())
