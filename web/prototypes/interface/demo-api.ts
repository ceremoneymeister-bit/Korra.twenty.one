// In-browser synthetic transport. No model, production account or backend.
// Unrecognised requests fail closed; they are never forwarded to the network.
const now = 1789552800;
const names = ["Корра", "Дизайнер", "Юрист", "Наставник", "Секретарь", "SMM-отдел", "Студия сайтов", "Финансист", "Аналитик", "Редактор", "Учитель китайского", "Оператор терминала"];
const ids = ["default", "designer", "lawyer", "mentor", "secretary", "smm", "web", "finance", "analytics", "editor", "teacher", "terminal"];
const pairs = [
  ["Помоги подготовить встречу с командой.", "Конечно. Что хотите обсудить на встрече?"],
  ["Новый сайт и план запуска на октябрь.", "Предлагаю три темы: готовность сайта, контент и сроки запуска."],
  ["Сколько времени заложить?", "45 минут: 15 на сайт, 20 на план и 10 на решения."],
  ["Добавь проверку мобильной версии.", "Добавил в обсуждение готовности сайта."],
  ["Кто должен участвовать?", "Руководитель проекта, дизайнер и ответственный за контент."],
  ["Зафиксируй следующий шаг.", "Собрать замечания команды к макету до пятницы."],
];
const long = "## План запуска сайта\n\nСначала проверим, что посетитель может пройти весь путь: понять предложение, посмотреть примеры и оставить заявку.\n\n### Что делаем на этой неделе\n\n| Работа | Результат | Срок |\n|---|---|---|\n| Проверка макета | Список замечаний команды | Вторник |\n| Подготовка текстов | Согласованные страницы услуг | Четверг |\n| Тестирование формы | Заявка приходит ответственному | Пятница |\n\n### Проверка перед запуском\n\n1. Открыть сайт на ноутбуке и телефоне.\n2. Пройти форму с тестовыми данными.\n3. Проверить ссылки, изображения и страницу подтверждения.\n\nПример структуры результата:\n\n```json\n{\n  \"проект\": \"Новый сайт\",\n  \"статус\": \"На проверке\",\n  \"следующий_шаг\": \"Собрать обратную связь\"\n}\n```\n\nПосле этого можно назначать дату публикации. Ответственного за каждую работу стоит определить на встрече.";
const titles = ["План запуска сайта на октябрь", "Структура проекта и задачи команды", "Материалы для встречи", "Договор с поставщиком: правки и сроки", "Контент-план на следующую неделю", "Письмо клиенту о запуске", "Презентация нового продукта", "Идеи для осенней кампании", "Сводка продаж за неделю", "Инструкция для нового менеджера", "Смета проекта и распределение бюджета", "Ответы на вопросы клиентов"];
const sessions = ids.flatMap((profile, p) => Array.from({ length: p ? 4 : 60 }, (_, i) => ({
  id: p ? `${profile}-${i}` : i === 0 ? "demo-short" : i === 1 ? "demo-long" : i === 2 ? "demo-files" : `demo-${i}`,
  profile, source: "dashboard", title: i === 59 ? "Архив: запуск весенней коллекции" : titles[i % titles.length],
  model: "demo", started_at: now - i * 76000, last_active: now - i * 76000,
  ended_at: null, is_active: false, message_count: i === 0 ? 12 : 2,
  tool_call_count: 0, input_tokens: 0, output_tokens: 0, preview: titles[i % titles.length],
}))).map(s => ({ ...s }));
const messages = new Map<string, Array<{ role: string; content: string; timestamp: number; id: number }>>();
for (const s of sessions) {
  const content = s.id === "demo-short" ? pairs.flatMap(([q,a]) => [{ role:"user", content:q }, { role:"assistant", content:a }])
    : [{ role:"user", content:s.id === "demo-long" ? "Составь план запуска сайта с таблицей и примером." : s.title }, { role:"assistant", content:s.id === "demo-long" ? long : s.id === "demo-files" ? "Подготовил повестку встречи. Файл можно открыть, скачать или прикрепить к следующему сообщению.\n\nMEDIA:\"/demo/workspace/Повестка встречи.md\"\n\nИ эскиз для обсуждения:\n\nMEDIA:\"/demo/workspace/Эскиз баннера.png\"" : "Материалы собраны. Следующий шаг — обсудить их с командой и уточнить сроки." }];
  messages.set(s.id, content.map((m,i) => ({ ...m, timestamp: now + i, id:i+1 })));
}
const files = ["Повестка встречи.md", "Бриф проекта.txt", "План запуска.md", "Эскиз баннера.png"];
const fileText = "# Повестка встречи\n\n1. Готовность сайта\n2. Контент и сроки\n3. Следующие шаги\n\nДемонстрационные материалы Korra.";

export function installDemo() {
  // The static preview host uses HTTP; the product normally uses HTTPS.
  if (!crypto.randomUUID) Object.defineProperty(crypto, "randomUUID", { value: () => {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  } });
  const params = new URLSearchParams(location.search);
  const base = location.pathname.replace(/\/index\.html$/, "").replace(/\/$/, "");
  Object.assign(window, { __HERMES_BASE_PATH__: base, __HERMES_SESSION_TOKEN__: "synthetic-preview-only", __KORRA_UI_MODE__: "fleet", __HERMES_DASHBOARD_BUBBLE_CHAT__: true });
  const uploads = new Map<string, { upload_id:string; origin:string; files:Array<{ path:string; size:number }> }>();
  const limits = { part_bytes:4194304, max_files:1000, max_directories:1000, max_file_bytes:2147483648, max_total_bytes:2147483648, chat_max_files:30, chat_folder_threshold:30 };
  const illustration = document.createElement("canvas"); illustration.width = 1200; illustration.height = 600;
  const ctx = illustration.getContext("2d")!;
  ctx.fillStyle = "#ddebba"; ctx.fillRect(0,0,1200,600);
  ctx.fillStyle = "#a8cf68"; ctx.beginPath(); ctx.arc(1060,580,390,0,Math.PI*2); ctx.fill();
  ctx.fillStyle = "#243222"; ctx.font = "bold 60px sans-serif"; ctx.fillText("Осень начинается",64,215); ctx.fillText("с новых идей",64,292);
  ctx.font = "24px sans-serif"; ctx.fillText("Демонстрационный эскиз · Korra",68,400);
  const imageBlob = new Promise<Blob>(resolve => illustration.toBlob(blob => resolve(blob!), "image/png"));
  let pref = { version: 1, known: true, theme: params.get("theme") === "dark" ? "dark" : "light", installation_id: "00000000000000000000000000000104", owner:"interface-demo", base_path:base, revision:"demo-1", evening:{ disabled:true, snooze_until:0 } };
  Object.assign(window, { __KORRA_THEME_PREF__: pref, __interfaceDemoRequests: [] });
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers:{ "Content-Type":"application/json" } });
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.href);
    const route = url.pathname.slice(url.pathname.indexOf("/api/") + 4);
    const profile = url.searchParams.get("profile") || "default";
    const method = init?.method || "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : {};
    (window as unknown as { __interfaceDemoRequests: string[] }).__interfaceDemoRequests.push(`${method} ${route}`);
    if (!url.pathname.includes("/api/")) return json({ error:"Доступно только на рабочей установке." }, 404);
    if (route === "/dashboard/themes") return json({ themes:[], preference:pref });
    if (route === "/dashboard/theme") { pref = { ...pref, theme:body.name || pref.theme, revision:String(Date.now()) }; return json({ ok:true, theme:pref.theme, preference:pref }); }
    if (route === "/dashboard/font") return json({ ok:true, font:"onest" });
    if (route === "/dashboard/plugins") return json([]);
    if (route === "/cabinet/session") return json({ kind:"client", capabilities:{ files_mkdir:true }, logout_url:"" });
    if (route === "/auth/me") return json({ error:"demo" }, 401);
    if (route === "/status") return json({ version:"0.21.7", gateway_running:true, gateway_state:"running", gateway_platforms:{}, active_sessions:0, overall:"ok", profile:"default", uptime_seconds:3600, can_update_hermes:false });
    if (route === "/profiles") return json({ profiles:ids.map((name,i) => ({ name, display_name:names[i], path:`/demo/profiles/${name}`, is_default:!i, gateway_running:!i, gateway_status:i ? "served" : "running", description:`Помощник: ${names[i]}`, description_auto:false, skill_count:3 })) });
    if (route === "/profiles/active") return json({ active:"default", current:"default" });
    if (route === "/chat/runs") return json({ runs:[] });
    if (route === "/chat/approvals") return json({ approvals:[] });
    if (route === "/sessions") {
      const list = sessions.filter(s => s.profile === profile);
      const limit = Number(url.searchParams.get("limit") || 50), offset = Number(url.searchParams.get("offset") || 0);
      return json({ sessions:list.slice(offset, offset+limit), total:list.length, limit, offset });
    }
    if (route === "/sessions/search") {
      const q = (url.searchParams.get("q") || "").toLocaleLowerCase("ru");
      const results = sessions.filter(s => s.profile === profile && (s.title.toLocaleLowerCase("ru").includes(q) || messages.get(s.id)?.some(m => m.content.toLocaleLowerCase("ru").includes(q))));
      return json({ results:results.map(s => {
        const hit = s.title.toLocaleLowerCase("ru").includes(q) ? undefined
          : messages.get(s.id)?.find(m => m.content.toLocaleLowerCase("ru").includes(q));
        return { ...s, session_id:s.id, snippet:hit?.content.slice(0,240) || s.title, role:hit?.role || null, session_started:s.started_at };
      }) });
    }
    const session = route.match(/^\/sessions\/([^/]+)(\/.*)?$/);
    if (session) {
      const id = decodeURIComponent(session[1]), item = sessions.find(s => s.id === id && s.profile === profile);
      if (session[2] === "/messages") return json({ session_id:id, messages:item ? messages.get(id) || [] : [] });
      if (session[2] === "/latest-descendant") return json({ session_id:id, requested_session_id:id, path:[id], changed:false });
      if (method === "PATCH") { const target = sessions.find(s => s.id === id && s.profile === (body.profile || "default")); if (target) target.title = body.title; return json({ ok:true, title:body.title }); }
      if (method === "DELETE") { if (item) sessions.splice(sessions.indexOf(item),1); return json({ ok:true }); }
      return json(item || {}, item ? 200 : 404);
    }
    if (route === "/files") return json({ root:"/demo/workspace", locked_root:"/demo/workspace", path:"/demo/workspace", parent:null, entries:files.map(name => ({ name, path:`/demo/workspace/${name}`, is_directory:false, size:280, mtime:now, mime_type:name.endsWith("png") ? "image/png" : "text/plain", revision:"demo-1" })), total:files.length });
    if (route === "/files/attachment") { const path = url.searchParams.get("path") || "", image = path.endsWith("png"); return json({ path, name:path.split("/").pop(), size:280, mime_type:image ? "image/png" : "text/plain", revision:"demo-1", kind:image ? "png" : "md", reader:image ? "vision" : "read_file" }); }
    if (route === "/files/read") return json({ path:url.searchParams.get("path"), content:fileText, encoding:"utf-8", size:280, truncated:false, revision:"demo-1" });
    if (route === "/files/text") return json({ path:url.searchParams.get("path"), name:url.searchParams.get("path")?.split("/").pop(), text:fileText, encoding:"utf-8", mime_type:"text/plain", language:"markdown", size:280, truncated:false, binary:false, editable:false, sha256:null, root:"/demo/workspace", locked_root:"/demo/workspace", can_change_path:false });
    if (route === "/files/raw" || route === "/files/download") return url.searchParams.get("path")?.endsWith("png") ? new Response(await imageBlob, { headers:{ "Content-Type":"image/png" } }) : new Response(fileText, { headers:{ "Content-Type":"text/plain; charset=utf-8" } });
    if (route === "/uploads" && method === "POST") {
      uploads.set(body.upload_id, body);
      // Synthetic completed receipts: device bytes never leave the browser.
      return json({ upload_id:body.upload_id, origin:body.origin, target:"/demo/workspace", published:false, received:body.files.map((f: {size:number},i:number) => ({ index:i, bytes:f.size, complete:true })), already_present:body.files.map((_:unknown,i:number) => i), limits });
    }
    if (route.startsWith("/uploads/")) {
      const upload = uploads.get(decodeURIComponent(route.split("/")[2]));
      if (!upload) return json({ detail:"Тестовая загрузка не найдена." },404);
      const result = { published:true, files:upload.files.map((f,i) => ({ index:i, path:`/demo/workspace/${f.path}`, name:f.path, kind:f.path.split(".").pop() || "txt", size:f.size, reader:"read_file", deduplicated:true, skipped:false })), folder:null, skipped:[], excluded:[] };
      return json(route.endsWith("/complete") ? result : { upload_id:upload.upload_id, origin:upload.origin, target:"/demo/workspace", published:true, result, received:[], already_present:[], limits });
    }
    if (route === "/chat/completions") {
      const headers = new Headers(init?.headers), id = headers.get("X-Hermes-Session-Id") || "demo-new";
      const answer = "Это демонстрационный ответ. В прототипе можно проверить ввод, остановку, переключение чатов и компоновку интерфейса.";
      let timer: ReturnType<typeof setInterval>, offset = 0;
      const stream = new ReadableStream({ start(controller) {
        const encode = new TextEncoder();
        timer = setInterval(() => {
          if (init?.signal?.aborted) { clearInterval(timer); controller.close(); return; }
          if (offset >= answer.length) { clearInterval(timer); controller.enqueue(encode.encode("data: [DONE]\n\n")); controller.close(); const history = messages.get(id) || []; messages.set(id,[...history, { ...body.messages.at(-1), timestamp:now, id:history.length+1 }, { role:"assistant", content:answer, timestamp:now+1, id:history.length+2 }]); return; }
          controller.enqueue(encode.encode(`data: ${JSON.stringify({ choices:[{ delta:{ content:answer.slice(offset,offset+4) }, index:0 }] })}\n\n`)); offset += 4;
        }, 85);
      }, cancel() { clearInterval(timer); } });
      return new Response(stream, { headers:{ "Content-Type":"text/event-stream" } });
    }
    if (route.endsWith("/cancel")) return json({ ok:true });
    return json({ error:"Этот раздел не входит в прототип чата." }, 404);
  };
}
