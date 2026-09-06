from pathlib import Path
from playwright.sync_api import sync_playwright
import json,hashlib
from urllib.parse import urlparse
import argparse
parser=argparse.ArgumentParser(description="Одинаковые сохранённые ответы в эталоне и кандидате; запись в серверы заблокирована.")
parser.add_argument('--after-port',type=int,default=9145)
parser.add_argument('--qa-dir',type=Path,default=Path('/root/Antigravity/projects/Korra 21/ui pack/qa'))
args=parser.parse_args()
qa=args.qa_dir
qa.mkdir(parents=True,exist_ok=True)
data=json.loads(Path(__file__).with_name("chat-readability-2026-09-06.samples.json").read_text())
cases=[(x['id'],x['title'],x['text'],x['origin']) for x in data]
for x in data:
 assert hashlib.sha256(x['text'].encode()).hexdigest()==x['sha256']
with sync_playwright() as w:
 b=w.chromium.launch(headless=True,args=['--no-sandbox'])
 for label,port in [('before',9123),('after',args.after_port)]:
  for ident,title,text,origin in cases:
   ctx=b.new_context(viewport={'width':1440,'height':1100},device_scale_factor=1)
   page=ctx.new_page(); errors=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   sid='astra-chat-readability-'+ident
   session={'id':sid,'source':'dashboard','model':None,'title':title,'started_at':1788660000,'last_active':1788660000,'ended_at':None,'is_active':False,'message_count':2,'tool_call_count':0,'input_tokens':0,'output_tokens':0,'preview':title}
   messages=[{'role':'user','content':title,'timestamp':1788660000}, {'role':'assistant','content':text,'timestamp':1788660001}]
   if ident=='tools':
    # Служебные события — воспроизведение формата истории, не новый запуск инструментов.
    messages.insert(1,{'role':'assistant','content':'','tool_calls':[{'id':'qa-read','type':'function','function':{'name':'read_file','arguments':'{"path":"/opt/data/config.yaml"}'}}]})
    messages.insert(2,{'role':'tool','tool_call_id':'qa-read','content':'Проверка чтения завершена.'})
   def route(r):
    path=urlparse(r.request.url).path
    if path=='/api/sessions':return r.fulfill(json={'sessions':[session],'total':1,'limit':80,'offset':0})
    if path==f'/api/sessions/{sid}/messages':return r.fulfill(json={'messages':messages,'total':len(messages)})
    if path==f'/api/sessions/{sid}/latest-descendant':return r.fulfill(json={'requested_session_id':sid,'session_id':sid,'path':[sid],'changed':False})
    if path==f'/api/sessions/{sid}':return r.fulfill(json=session)
    if path=='/api/chat/approvals':return r.fulfill(json={'approvals':[]})
    if r.request.method not in ('GET','HEAD','OPTIONS'):return r.fulfill(status=200,json={})
    return r.continue_()
   page.route('**/*',route)
   page.goto(f'http://127.0.0.1:{port}/agents?resume={sid}')
   page.locator('.korra-markdown').first.wait_for(timeout=30000)
   page.evaluate('document.fonts.ready')
   page.wait_for_timeout(300)
   page.evaluate('''()=>{let el=document.querySelector('.korra-chat-transcript__content');while(el && !(el.scrollHeight>el.clientHeight && ['auto','scroll'].includes(getComputedStyle(el).overflowY)))el=el.parentElement; if(el)el.scrollTop=0;}''')
   page.wait_for_timeout(150)
   page.screenshot(path=str(qa/f'astra-chat-2-{ident}-{label}.png'))
   metrics=page.locator('.korra-markdown').first.evaluate('(el)=>({font:getComputedStyle(el).fontFamily,size:getComputedStyle(el).fontSize,line:getComputedStyle(el).lineHeight,width:el.clientWidth,headings:el.querySelectorAll("h1,h2,h3").length,overflow:document.documentElement.scrollWidth>innerWidth})')
   assert not errors
   assert not metrics['overflow']
   print(label,ident,metrics,'errors',errors,flush=True)
   ctx.close()
 b.close()
