from pathlib import Path
from playwright.sync_api import sync_playwright
from urllib.parse import urlparse
import json
import argparse
parser=argparse.ArgumentParser(description="Тёмная тема, телефон и управляемый поток без запросов к модели.")
parser.add_argument('--after-port',type=int,default=9145)
parser.add_argument('--qa-dir',type=Path,default=Path('/root/Antigravity/projects/Korra 21/ui pack/qa'))
args=parser.parse_args()
qa=args.qa_dir
qa.mkdir(parents=True,exist_ok=True)
cases={x['id']:x for x in json.loads(Path(__file__).with_name("chat-readability-2026-09-06.samples.json").read_text())}
results=[]
with sync_playwright() as w:
 b=w.chromium.launch(headless=True,args=['--no-sandbox'])
 for label,port in [('before',9123),('after',args.after_port)]:
  for ident,theme,width in [('table','light',390),('code','dark',390),('long','dark',1440),('stream','light',1440)]:
   ctx=b.new_context(viewport={'width':width,'height':950 if width>600 else 844})
   ctx.add_init_script("localStorage.setItem('hermes-dashboard-theme',"+json.dumps(theme)+");")
   # Управляемые порции SSE проходят через настоящий обработчик потока интерфейса.
   ctx.add_init_script('''const nativeFetch=window.fetch.bind(window);window.fetch=async function(input,init){if(String(input).includes('/api/chat/completions') && init?.method==='POST'){let control;const body=new ReadableStream({start(c){control=c}});window.qaPush=s=>control.enqueue(new TextEncoder().encode(s));window.qaEnd=()=>control.close();return new Response(body,{headers:{'Content-Type':'text/event-stream'}})}return nativeFetch(input,init)}''')
   page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   sid='astra-chat-check-'+ident
   text=cases['long' if ident=='stream' else ident]['text']
   session={'id':sid,'source':'dashboard','model':None,'title':'Проверка чтения','started_at':1788660000,'last_active':1788660000,'ended_at':None,'is_active':False,'message_count':2,'tool_call_count':0,'input_tokens':0,'output_tokens':0,'preview':'Проверка чтения'}
   messages=[{'role':'user','content':'Проверка чтения','timestamp':1788660000},{'role':'assistant','content':text,'timestamp':1788660001}]
   def route(r):
    path=urlparse(r.request.url).path
    if path=='/api/sessions':return r.fulfill(json={'sessions':[session],'total':1,'limit':80,'offset':0})
    if path==f'/api/sessions/{sid}/messages':return r.fulfill(json={'messages':messages,'total':len(messages)})
    if path=='/api/dashboard/themes':return r.fulfill(json={'active':theme,'themes':[]})
    if path=='/api/chat/approvals':return r.fulfill(json={'approvals':[]})
    if r.request.method not in ('GET','HEAD','OPTIONS'):return r.fulfill(json={})
    return r.continue_()
   page.route('**/*',route)
   page.goto(f'http://127.0.0.1:{port}/agents?resume={sid}')
   page.locator('.korra-markdown').first.wait_for(timeout=30000);page.evaluate('document.fonts.ready');page.wait_for_timeout(300)
   page.evaluate('''()=>{let el=document.querySelector('.korra-chat-transcript__content');while(el && !['auto','scroll'].includes(getComputedStyle(el).overflowY))el=el.parentElement;window.qaViewport=el;el.scrollTop=0;}''')
   record={'variant':ident,'surface':label,'theme':theme,'width':width,'errors':errors}
   if ident=='stream':
    page.get_by_role('textbox',name='Сообщение Корре',exact=True).fill('Покажи тот же пример с расчётом.')
    page.get_by_role('button',name='Отправить',exact=True).click();page.wait_for_function('!!window.qaPush')
    def event(typ,data):
     s=('event: '+typ+'\n' if typ else '')+'data: '+json.dumps(data,ensure_ascii=False)+'\n\n'
     page.evaluate('s=>window.qaPush(s)',s);page.wait_for_timeout(120)
    event('hermes.tool.progress',{'tool':'terminal','toolCallId':'qa-one','status':'running','label':'python -c "print(120000 * 0.18)"'})
    if label=='after':assert page.locator('article').count()==1,'Не должно быть пустой карточки ответа'
    page.screenshot(path=str(qa/f'astra-chat-2-stream-tools-{label}.png'))
    event('hermes.tool.progress',{'tool':'terminal','toolCallId':'qa-one','status':'completed'})
    event('',{'id':'qa','object':'chat.completion.chunk','choices':[{'index':0,'delta':{'content':text[:2000]},'finish_reason':None}]})
    if label=='after':
     assert page.get_by_role('button',name='Пишу ответ…').get_attribute('aria-expanded')=='false'
    page.evaluate('window.qaViewport.scrollTop=160');page.wait_for_timeout(120)
    before=page.evaluate('window.qaViewport.scrollTop')
    event('',{'id':'qa','object':'chat.completion.chunk','choices':[{'index':0,'delta':{'content':text[2000:]},'finish_reason':None}]})
    after=page.evaluate('window.qaViewport.scrollTop')
    record.update(scrollBefore=before,scrollAfter=after)
    if label=='after':
     assert abs(after-before)<2,'Поток оторвал человека от чтения'
     assert page.get_by_role('button',name='К последнему ответу',exact=True).is_visible()
    page.screenshot(path=str(qa/f'astra-chat-2-stream-reading-{label}.png'))
    if label=='after':page.get_by_role('button',name='К последнему ответу',exact=True).click()
    event('',{'id':'qa','object':'chat.completion.chunk','choices':[{'index':0,'delta':{},'finish_reason':'stop'}]})
    page.evaluate("window.qaPush('data: [DONE]\\n\\n');window.qaEnd()")
    page.wait_for_timeout(200)
   else:
    if ident=='long' and label=='after':assert page.locator('.korra-markdown__table-hint').count()==0
    page.screenshot(path=str(qa/f'astra-chat-2-{ident}-{theme}-{width}-{label}.png'))
    record.update(page.evaluate('''()=>({pageOverflow:document.documentElement.scrollWidth>innerWidth,font:getComputedStyle(document.querySelector('.korra-markdown')).fontFamily,background:getComputedStyle(document.documentElement).getPropertyValue('--neo-background'),tableWidth:document.querySelector('.korra-markdown__table')?.clientWidth,tableScroll:document.querySelector('.korra-markdown__table')?.scrollWidth,codeFont:document.querySelector('pre code') && getComputedStyle(document.querySelector('pre code')).fontFamily})'''))
    assert not record['pageOverflow']
    if ident=='table':
     if label=='after':assert page.get_by_text('Таблицу можно прокручивать вбок',exact=True).is_visible()
     assert record['tableScroll']>record['tableWidth']
     page.locator('.korra-markdown__table').evaluate('(el)=>el.scrollLeft=el.scrollWidth')
     assert page.locator('.korra-markdown__table').evaluate('(el)=>el.scrollLeft')>0
    if ident=='code' and label=='after':
     # localhost даёт защищённый буфер обмена; разрешение действует только в этом браузере.
     ctx.grant_permissions(['clipboard-read','clipboard-write'])
     code=page.locator('pre code').inner_text()
     page.get_by_role('button',name='Скопировать код',exact=True).click()
     assert page.evaluate('navigator.clipboard.readText()')==code
     record['clipboardExact']=True
   assert not errors
   print(record,flush=True);results.append(record);ctx.close()
 b.close()
print(json.dumps(results,ensure_ascii=False,indent=2))
