"""Isolated OpenAI-compatible delayed provider; never used by live contours."""
import asyncio, json, re, time
from aiohttp import web
calls = []
async def complete(request):
    body = await request.json()
    prompt = next((m.get('content', '') for m in reversed(body.get('messages', [])) if m.get('role') == 'user'), '')
    if not isinstance(prompt, str): prompt = str(prompt)
    marker = re.search(r'SESSION_TEST_[A-Z0-9_]+', prompt)
    text = 'Готово: ' + (marker.group() if marker else 'проверка') + '. Ответ сохранён.'
    delay = 14 if marker else 0
    calls.append({'marker': marker.group() if marker else 'auxiliary', 'time': time.time()})
    if not body.get('stream'):
        await asyncio.sleep(delay)
        return web.json_response({'id':'mock', 'object':'chat.completion','model':body.get('model'), 'choices':[{'index':0,'message':{'role':'assistant','content':text},'finish_reason':'stop'}], 'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
    response=web.StreamResponse(headers={'Content-Type':'text/event-stream'})
    await response.prepare(request)
    for piece in [text[:8],text[8:]]:
        await response.write(('data: '+json.dumps({'id':'mock','object':'chat.completion.chunk','choices':[{'index':0,'delta':{'content':piece},'finish_reason':None}]},ensure_ascii=False)+'\n\n').encode())
        await asyncio.sleep(delay / 2)
    await response.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    return response
async def status(request): return web.json_response({'calls':calls})
app=web.Application();app.router.add_post('/v1/chat/completions',complete);app.router.add_get('/status',status)
web.run_app(app,host='127.0.0.1',port=8677)
