from pathlib import Path
from playwright.sync_api import sync_playwright
import base64,html
import argparse
parser=argparse.ArgumentParser(description="Пары исходных снимков в одном кадре, без изменения текста и масштаба чата.")
parser.add_argument('--qa-dir',type=Path,default=Path('/root/Antigravity/projects/Korra 21/ui pack/qa'))
qa=parser.parse_args().qa_dir
font_path=Path(__file__).resolve().parents[2]/'web/public/fonts/Onest-Variable.woff2'
font_data=base64.b64encode(font_path.read_bytes()).decode()
titles={'short':'Короткий ответ','list':'План действий','table':'Сравнение и таблица','code':'Код и копирование','long':'Длинный ответ с заголовками','error':'Ответ с ошибкой','tools':'Ход работы и результат','table-light-390':'Таблица на телефоне','code-dark-390':'Код на телефоне · тёмная тема','long-dark-1440':'Длинный ответ · тёмная тема','stream-tools':'Поток · выполняется инструмент','stream-reading':'Поток · читаем предыдущий текст'}
with sync_playwright() as w:
 b=w.chromium.launch(headless=True,args=['--no-sandbox'])
 for key,title in titles.items():
  mobile='390' in key
  frame_width=390 if mobile else 930
  frame_height=844 if mobile else (790 if key in ['long-dark-1440','stream-tools','stream-reading'] else 940)
  page=b.new_page(viewport={'width':2*frame_width+48,'height':frame_height+94},device_scale_factor=1)
  cards=[]
  for side,label in [('before','До · действующая панель'),('after','После · кандидат')]:
   path=qa/f'astra-chat-2-{key}-{side}.png'
   src='data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode()
   crop='' if mobile else 'margin-left:-495px;margin-top:-140px;'
   cards.append(f'<section><h2>{label}</h2><div class="frame"><img src="{src}" style="{crop}"></div></section>')
  page.set_content(f'''<html lang="ru"><style>@font-face{{font-family:Onest;src:url(data:font/woff2;base64,{font_data}) format('woff2')}}*{{box-sizing:border-box}}body{{margin:0;padding:12px;background:#e8e8e8;color:#1f1f1f;font-family:Onest,Arial,sans-serif}}h1{{font-size:18px;margin:0 0 8px}}h2{{font-size:14px;margin:0 0 8px;font-weight:400}}main{{display:flex;gap:24px}}.frame{{width:{frame_width}px;height:{frame_height}px;overflow:hidden}}img{{max-width:none;display:block}}</style><h1>{html.escape(title)} · один и тот же текст</h1><main>{''.join(cards)}</main></html>''')
  page.evaluate('document.fonts.ready')
  page.screenshot(path=str(qa/f'astra-chat-2-{key}-pair.png'));page.close()
 b.close()
print('Создано пар:',len(titles))
