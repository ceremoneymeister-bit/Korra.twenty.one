from playwright.sync_api import sync_playwright, expect
from pathlib import Path
import json
OUT=Path('/opt/korra-21/ui pack/qa')
proof={'scenario':'Итоговый мастер → ответ → навыки того же агента без перезагрузки', 'container':'astra-agents', 'panel':9142, 'api':8672}
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,args=['--no-sandbox'])
 page=b.new_page(viewport={'width':1440,'height':1000})
 page.set_default_timeout(45000)
 page.goto('http://127.0.0.1:9142/profiles',wait_until='networkidle')
 page.get_by_role('button',name='Создать агента',exact=True).click()
 page.locator('#pb-name').fill('Помощник склада')
 page.locator('#pb-role').fill('Помогаешь владельцу склада разбирать заказы. Минимальный заказ — 18 изделий. Если клиент просит меньше, объясни ограничение. Отвечай по-русски и коротко. Готовь текст для владельца, самостоятельно сообщения клиентам не отправляй.')
 profile=page.locator('#pb-id').input_value()
 proof['profile']=profile
 page.screenshot(path=str(OUT/'agents-astra-final-wizard.png'),full_page=True)
 catalog=[]
 page.on('request',lambda r: catalog.append(r.url) if r.url.endswith('/api/profiles') and r.method=='GET' else None)
 with page.expect_response(lambda r:'/api/profiles' in r.url and r.request.method=='POST') as creation:
  page.get_by_role('button',name='Создать агента',exact=True).click()
 proof['createStatus']=creation.value.status
 proof['createRequest']=creation.value.request.post_data_json
 page.get_by_role('status').filter(has_text='Агент отвечает:').wait_for(timeout=90000)
 proof['probeText']=page.get_by_role('status').filter(has_text='Агент отвечает:').inner_text()
 proof['catalogReloadsAfterPost']=len(catalog)
 page.screenshot(path=str(OUT/'agents-astra-final-created.png'),full_page=True)
 page.get_by_role('button',name='Открыть чат',exact=True).click()
 tab=page.locator('#agent-tab-'+profile)
 expect(tab).to_have_attribute('aria-selected','true')
 textarea=page.locator('textarea:visible').last
 proof['composerPlaceholder']=textarea.get_attribute('placeholder')
 assert 'Помощник склада' in proof['composerPlaceholder']
 textarea.fill('Клиент просит 7 изделий. Можно ли принять заказ? Ответь коротко.')
 with page.expect_response(lambda r:'/api/chat/completions' in r.url and r.request.method=='POST',timeout=90000) as chat:
  textarea.press('Enter')
 proof['chatStatus']=chat.value.status
 page.wait_for_function("document.body.innerText.includes('18')",timeout=90000)
 page.get_by_role('button',name='Остановить генерацию',exact=True).wait_for(state='hidden',timeout=90000)
 proof['chatVisibleText']=page.locator('body').inner_text()
 page.screenshot(path=str(OUT/'agents-astra-final-chat.png'),full_page=True)
 # Переход по настоящему меню агента: настройки → навыки, без перезагрузки SPA.
 tab.locator('..').locator('[data-agent-tab-menu-trigger]').click()
 page.get_by_role('menuitem',name='Роль и поведение',exact=True).click()
 expect(page.locator('#profile-soul-editor')).to_have_value(proof['createRequest']['soul'])
 page.get_by_role('button',name='Закрыть',exact=True).click()
 # Адресная навигация сохраняет весь экземпляр приложения и загруженный каталог.
 requests=[]
 page.on('request',lambda r: requests.append(r.url) if '/api/skills' in r.url else None)
 page.evaluate("id=>{history.pushState({},'', '/skills?profile='+encodeURIComponent(id));dispatchEvent(new PopStateEvent('popstate'));}",profile)
 page.wait_for_timeout(1800)
 proof['skillsRequests']=requests
 proof['skillsScope']=page.locator('#korra-profile-scope').inner_text()
 assert requests and all('profile='+profile in url for url in requests)
 assert 'Помощник склада' in proof['skillsScope']
 page.screenshot(path=str(OUT/'agents-astra-final-skills-scope.png'),full_page=True)
 # Проверяем согласованную ссылку карточки основного агента после другого чата.
 page.evaluate("history.pushState({},'', '/profiles');dispatchEvent(new PopStateEvent('popstate'))")
 page.get_by_role('button',name='Открыть чат',exact=True).first.click()
 expect(page.locator('#agent-tab-')).to_have_attribute('aria-selected','true')
 proof['defaultCardOpensMainChat']=True
 assert proof['createStatus']==200 and proof['chatStatus']==200 and proof['catalogReloadsAfterPost']>=1
 Path('/tmp/astra-agents-final-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
 print(json.dumps(proof,ensure_ascii=False,indent=2))
 b.close()
