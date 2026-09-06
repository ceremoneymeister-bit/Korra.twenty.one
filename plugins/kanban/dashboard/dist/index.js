/* Доска поручений Korra. Статусы и запуск принадлежат существующему серверу канбана.
   Совместимые имена SDK и плагина сохранены для старых установок. */
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  const { React } = SDK;
  const h = React.createElement;
  const { Link, useSearchParams, useHref } = SDK.router;
  const { useState, useEffect, useRef, useCallback } = React;
  const C = SDK.components;
  const API = "/api/plugins/kanban";
  const STATUS = {
    triage: ["Уточняется", "Агент готовит подробное задание из идеи."],
    todo: ["Ждёт других задач", "Продолжится после выполнения зависимостей."],
    scheduled: ["Отложено", "Ожидает назначенного времени продолжения."],
    ready: ["Очередь", "Агент начнёт, когда освободится."],
    running: ["В работе", "Агент выполняет поручение."],
    blocked: ["Нужно решение", "Прочитайте причину и помогите агенту продолжить."],
    review: ["На проверке", "Откройте результат: примите или верните на доработку."],
    done: ["Готово", "Завершённые задачи с сохранённым результатом."],
    archived: ["Архив", "Задачи убраны с основной доски."],
  };
  const PRIMARY = ["ready", "running", "blocked", "review", "done"];
  const ACTION = { ready: "Передать агенту", blocked: "Приостановить", done: "Принять результат", archived: "В архив" };
  const RUN_LABEL = { running: "В работе", completed: "Завершён", done: "Завершён", success: "Успешно", failed: "Ошибка", error: "Ошибка", blocked: "Нужно решение", review: "На проверке", reclaimed: "Остановлен", cancelled: "Отменён", timeout: "Время истекло", gave_up: "Требуется помощь", scheduled: "Отложен", claimed: "Запускается" };

  function statusLabel(status) { return (STATUS[status] || ["Другой этап"])[0]; }
  function completionLabel(task) { return task.status === "review" ? "Принять результат" : "Завершить поручение"; }
  function profileLabel(profile) { return profile.display_name || (profile.is_default || profile.name === "default" ? "Корра" : profile.name); }
  function dateLabel(value) {
    if (!value) return "ещё не было";
    const date = new Date(typeof value === "number" ? value * 1000 : value);
    return Number.isNaN(date.getTime()) ? "дата неизвестна" : date.toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }
  function errorText(error) {
    const raw = String(error && error.message || error || "");
    if (/parent|dependenc|prerequisite/i.test(raw)) return "Сначала завершите связанные задачи. Их список есть в карточке.";
    if (/claim|running|active run/i.test(raw)) return "Агент уже работает над задачей. Сначала приостановите её, затем измените назначение.";
    if (/409|already exists/i.test(raw)) return "Данные изменились. Обновите доску и повторите действие.";
    if (/404/.test(raw)) return "Задача или доска больше недоступна. Обновите список.";
    if (/403|401/.test(raw)) return "Не удалось подтвердить доступ. Перезагрузите страницу.";
    if (/413/.test(raw)) return "Файл слишком большой. Выберите файл меньшего размера.";
    return "Не удалось выполнить действие. Проверьте соединение и повторите попытку.";
  }
  function runErrorText(error) {
    if (/401|403|oauth|token.*expired|authentication/i.test(String(error))) return "Сервис ответов отклонил подключение. Нужно восстановить доступ к нему; само поручение сохранено.";
    if (/429|503|rate.?limit|cool.?down/i.test(String(error))) return "Сервис ответов временно недоступен или достиг лимита. Поручение сохранено — его можно повторить после восстановления сервиса.";
    return "Во время выполнения возникла ошибка. Поручение и вложения сохранены; проверьте последний результат перед повтором.";
  }
  function request(path, board, method, payload) {
    return SDK.fetchJSON(API + path + (path.includes("?") ? "&" : "?") + "board=" + encodeURIComponent(board), method ? {
      method, headers: { "Content-Type": "application/json" },
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
    } : undefined);
  }
  function Button(props) {
    const { children, primary, ...rest } = props;
    return h("button", { type: "button", className: "neo-button", "data-neo-variant": primary ? "primary" : undefined, ...rest }, children);
  }
  function Field({ label, children, hint }) {
    const id = React.useId();
    return h("div", { className: "k21-field" }, h("label", { htmlFor: id }, label),
      React.cloneElement(children, { id, "aria-describedby": hint ? id + "-hint" : undefined }),
      hint && h("small", { id: id + "-hint" }, hint));
  }
  function AgentSelect({ value, onChange, profiles, required, disabled, id, "aria-describedby": describedBy }) {
    const known = profiles.some(profile => profile.name === value);
    return h("select", { id, "aria-describedby": describedBy, value, onChange: event => onChange(event.target.value), required, disabled },
      h("option", { value: "" }, "Выберите агента"),
      value && !known && h("option", { value }, value + " (недоступен)"),
      profiles.map(profile => h("option", { key: profile.name, value: profile.name }, profileLabel(profile))));
  }
  function Modal({ title, description, onClose, busy, children }) {
    return h(C.Dialog, { open: true, onOpenChange: open => { if (!open && !busy) onClose(); } },
      h(C.DialogContent, { className: "k21-board-modal", showCloseButton: false },
        h("div", { className: "k21-modal-heading" },
          h(C.DialogTitle, { className: "k21-modal-title" }, title),
          h(Button, { onClick: onClose, disabled: busy, "aria-label": "Закрыть окно" }, "Закрыть")),
        h(C.DialogDescription, { className: "k21-muted" }, description), children));
  }

  function RichText({ children }) {
    return C.Markdown ? h(C.Markdown, { content: String(children || "") }) : h("p", { className: "k21-preserve" }, children);
  }

  function TaskForm({ profiles, board, onClose, onSaved, task, boardMeta }) {
    const [title, setTitle] = useState(task ? task.title : "");
    const [body, setBody] = useState(task ? task.body || "" : "");
    const [assignee, setAssignee] = useState(task ? task.assignee || "" : "");
    const [tenant, setTenant] = useState(task ? task.tenant || "" : "");
    const [priority, setPriority] = useState(task ? task.priority || 0 : 0);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [files, setFiles] = useState([]);
    const fileInput = useRef(null);
    const draftId = useRef(null);
    const uploaded = useRef(new Set());
    const handoffStarted = useRef(false);
    const key = useRef(null);
    if (!key.current) key.current = "panel-" + crypto.randomUUID();
    const saving = useRef(false);
    async function submit(event) {
      event.preventDefault();
      if (saving.current || !title.trim() || !body.trim() || !assignee) return;
      saving.current = true; setBusy(true); setError("");
      try {
        if (handoffStarted.current && draftId.current) {
          const current = await request("/tasks/" + encodeURIComponent(draftId.current), board);
          if (current.task?.assignee) { onSaved("Поручение уже передано. Проверьте его состояние в карточке.", draftId.current); return; }
          handoffStarted.current = false;
        }
        const payload = { title: title.trim(), body: body.trim(), assignee, priority: Number(priority) || 0 };
        if (!task) {
          payload.tenant = tenant.trim() || null;
          payload.idempotency_key = key.current;
          // Используем область доски, если она задана: результат в файлах сохраняется.
          if (boardMeta && boardMeta.default_workdir) {
            payload.workspace_kind = boardMeta.default_workspace_kind || "dir";
            payload.workspace_path = boardMeta.default_workdir;
          }
        }
        // Сначала прикрепляем исходные файлы, затем назначаем исполнителя.
        // Без назначения диспетчер не запустит незавершённую передачу.
        const hasFiles = !task && files.length > 0;
        const result = await request(task || draftId.current ? "/tasks/" + encodeURIComponent(task ? task.id : draftId.current) : "/tasks", board, task || draftId.current ? "PATCH" : "POST", hasFiles ? { ...payload, assignee: null } : payload);
        if (hasFiles) {
          draftId.current = draftId.current || result.task?.id;
          if (!draftId.current) throw new Error("Не получен номер поручения");
          for (const file of files) {
            if (uploaded.current.has(file)) continue;
            const form = new FormData(); form.append("file", file);
            const response = await SDK.authedFetch(API + "/tasks/" + encodeURIComponent(draftId.current) + "/attachments?board=" + encodeURIComponent(board), { method: "POST", body: form });
            if (!response.ok) throw new Error(String(response.status));
            uploaded.current.add(file);
          }
          handoffStarted.current = true;
          await request("/tasks/" + encodeURIComponent(draftId.current), board, "PATCH", { assignee });
        }
        onSaved(result.warning ? "Задача сохранена, но автоматический запуск сейчас недоступен. Проверьте настройки выполнения." : task ? "Изменения сохранены." : "Поручение добавлено в очередь. Агент может начать его автоматически.", draftId.current || result.task && result.task.id);
      } catch (err) { setError((handoffStarted.current ? "Не удалось подтвердить передачу. Повторите запрос или откройте сохранённую карточку. " : draftId.current ? "Поручение сохранено без запуска. " : "") + errorText(err)); }
      finally { saving.current = false; setBusy(false); }
    }
    function close() { if (draftId.current) onSaved(handoffStarted.current ? "Проверьте состояние передачи в карточке поручения." : "Поручение сохранено без исполнителя. Его можно дополнить и передать агенту позже.", draftId.current); else onClose(); }
    return h(Modal, { title: task ? "Изменить поручение" : "Новое поручение", description: task ? "Уточните задание и ожидаемый результат." : "Агент получит задание вместе с исходными файлами. После передачи он сможет начать работу автоматически.", busy, onClose: close },
      h("form", { onSubmit: event => void submit(event), className: "k21-form" },
        h(Field, { label: "Что нужно сделать" }, h("input", { value: title, onChange: e => setTitle(e.target.value), required: true, maxLength: 300, placeholder: "Например, сравнить предложения трёх поставщиков" })),
        h(Field, { label: "Задание и ожидаемый результат", hint: "Укажите исходные данные, ограничения и что вы хотите получить. Ссылки можно вставить прямо сюда." },
          h("textarea", { value: body, onChange: e => setBody(e.target.value), required: true, rows: 5, placeholder: "Сравни цену, сроки и условия. Результат — таблица и рекомендация с объяснением." })),
        h(Field, { label: "Кому поручить" }, h(AgentSelect, { value: assignee, onChange: setAssignee, profiles, required: true, disabled: task && task.status === "running" })),
        !task && h("div", { className: "k21-field" },
          h("input", { type: "file", multiple: true, hidden: true, ref: fileInput, "aria-label": "Исходные файлы", disabled: busy || !!draftId.current,
            onChange: event => setFiles(Array.from(event.target.files || [])) }),
          h(Button, { disabled: busy || !!draftId.current, onClick: () => fileInput.current.click() }, files.length ? "Изменить исходные файлы" : "Прикрепить исходные файлы"),
          files.map((file, index) => h("small", { key: index }, file.name, uploaded.current.has(file) ? " · загружен" : ""))),
        profiles.length === 0 && h("p", { className: "k21-muted" }, "Не удалось получить список агентов. Закройте окно, обновите доску и повторите."),
        h("details", null, h("summary", null, "Дополнительно"),
          !task && h(Field, { label: "Проект или клиент", hint: "Необязательная метка для поиска на доске." }, h("input", { value: tenant, onChange: e => setTenant(e.target.value), placeholder: "Например, магазин на Лесной" })),
          h(Field, { label: "Приоритет" }, h("select", { value: priority, onChange: e => setPriority(Number(e.target.value)) },
            h("option", { value: 0 }, "Обычный"), h("option", { value: 10 }, "Срочный — раньше остальных"),
            ![0, 10].includes(Number(priority)) && h("option", { value: priority }, "Сохранённый приоритет: " + priority)))),
        !task && !(boardMeta && boardMeta.default_workdir) && h("p", { className: "k21-muted" }, "Текст результата и вложения сохранятся в карточке поручения."),
        error && h("p", { role: "alert", className: "k21-error" }, error),
        h("div", { className: "k21-actions" }, h(Button, { onClick: close, disabled: busy }, "Отмена"),
          h(Button, { type: "submit", primary: true, disabled: busy || !title.trim() || !body.trim() || !assignee }, busy ? "Сохраняем…" : task ? "Сохранить" : "Передать агенту"))));
  }

  function MoveDialog({ task, target, profiles, board, onClose, onSaved }) {
    const [text, setText] = useState(target === "done" && task.status === "review" ? task.result || task.latest_summary || "" : "");
    const [assignee, setAssignee] = useState(task.assignee || "");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const saving = useRef(false);
    const needsText = target === "done" || target === "blocked" || (target === "ready" && task.status === "review");
    const action = target === "done" ? completionLabel(task) : target === "ready" && task.status === "review" ? "Вернуть на доработку" : ACTION[target];
    const explanation = {
      ready: "Задача вернётся в очередь и сможет запуститься автоматически. Если она уже выполняется, текущая попытка будет остановлена.",
      blocked: "Текущая попытка будет остановлена. Задача останется ждать вашего решения.",
      done: "Итог сохранится в карточке. Связанные задачи смогут продолжиться; работающий над этой задачей агент будет остановлен.",
      archived: "Карточка исчезнет с основной доски, но сохранится в архиве. Текущая работа над ней будет остановлена.",
    }[target];
    async function submit(event) {
      event.preventDefault();
      if (saving.current || (needsText && !text.trim()) || (target === "ready" && !assignee)) return;
      saving.current = true; setBusy(true); setError("");
      try {
        const patch = { status: target };
        if (target === "done") { patch.summary = text.trim(); patch.result = text.trim(); }
        if (target === "blocked") patch.block_reason = text.trim();
        if (target === "ready") {
          if (assignee !== task.assignee) patch.assignee = assignee;
          // Комментарий сохраняется до возобновления: агент увидит причину доработки.
          if (text.trim()) await request("/tasks/" + encodeURIComponent(task.id) + "/comments", board, "POST", { body: text.trim(), author: "Пользователь" });
        }
        await request("/tasks/" + encodeURIComponent(task.id), board, "PATCH", patch);
        onSaved("Состояние задачи обновлено.");
      } catch (err) { setError(errorText(err)); }
      finally { saving.current = false; setBusy(false); }
    }
    return h(Modal, { title: action, description: explanation, busy, onClose },
      h("form", { className: "k21-form", onSubmit: event => void submit(event) },
        h("strong", null, task.title),
        target === "ready" && h(Field, { label: "Исполнитель" }, h(AgentSelect, { value: assignee, onChange: setAssignee, profiles, required: true })),
        (needsText || target === "ready") && h(Field, { label: target === "done" ? "Что получилось" : target === "blocked" ? "Причина паузы" : "Что нужно уточнить или исправить", hint: target === "ready" ? "Комментарий останется в истории, даже если вернуть задачу в очередь не удастся." : undefined },
          h("textarea", { value: text, onChange: e => setText(e.target.value), required: needsText, rows: 5 })),
        error && h("p", { role: "alert", className: "k21-error" }, error),
        h("div", { className: "k21-actions" }, h(Button, { onClick: onClose, disabled: busy }, "Отмена"),
          h(Button, { type: "submit", primary: true, disabled: busy || (needsText && !text.trim()) || (target === "ready" && !assignee) }, busy ? "Сохраняем…" : action))));
  }

  function BoardSettings({ board, creating, onClose, onSaved }) {
    const [name, setName] = useState(creating ? "" : board.name || "");
    const [description, setDescription] = useState(creating ? "" : board.description || "");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const slug = useRef(creating ? "board-" + crypto.randomUUID().slice(0, 8) : board.slug);
    async function submit(event) {
      event.preventDefault(); if (busy || !name.trim()) return;
      setBusy(true); setError("");
      try {
        const payload = { name: name.trim(), description: description.trim() };
        if (creating) payload.slug = slug.current;
        await request(creating ? "/boards" : "/boards/" + encodeURIComponent(board.slug), board.slug, creating ? "POST" : "PATCH", payload);
        onSaved({ ...board, ...payload, slug: slug.current });
      } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
    }
    return h(Modal, { title: creating ? "Новая доска" : "Настройки доски", description: "Разделяйте работу по бизнес-процессам, проектам или клиентам. У каждой доски свой список задач.", busy, onClose },
      h("form", { className: "k21-form", onSubmit: event => void submit(event) },
        h(Field, { label: "Название доски" }, h("input", { value: name, onChange: e => setName(e.target.value), required: true, placeholder: "Например, продажи" })),
        h(Field, { label: "Для каких задач" }, h("textarea", { value: description, onChange: e => setDescription(e.target.value), rows: 3, placeholder: "Что команда делает на этой доске" })),
        error && h("p", { role: "alert", className: "k21-error" }, error),
        h("div", { className: "k21-actions" }, h(Button, { onClick: onClose, disabled: busy }, "Отмена"), h(Button, { type: "submit", primary: true, disabled: busy || !name.trim() }, busy ? "Сохраняем…" : creating ? "Создать доску" : "Сохранить"))));
  }

  function TaskDetail({ taskId, board, profiles, onClose, onMove, onEdit, onRefresh, onOpenTask }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState("");
    const [comment, setComment] = useState("");
    const [busy, setBusy] = useState(false);
    const [version, setVersion] = useState(0);
    const uploadInput = useRef(null);
    const taskAddress = useHref("/kanban?" + new URLSearchParams({ board, task: taskId }));
    const [copied, setCopied] = useState(false);
    useEffect(function () {
      let live = true;
      const refresh = () => request("/tasks/" + encodeURIComponent(taskId), board).then(result => { if (live) setData(result); }).catch(err => { if (live) setError(errorText(err)); });
      void refresh();
      const timer = setInterval(() => { if (document.visibilityState !== "hidden") void refresh(); }, 10000);
      return function () { live = false; clearInterval(timer); };
    }, [taskId, board, version]);
    async function addComment(event) {
      event.preventDefault(); if (busy || !comment.trim()) return;
      setBusy(true); setError("");
      try {
        await request("/tasks/" + encodeURIComponent(taskId) + "/comments", board, "POST", { body: comment.trim(), author: "Пользователь" });
        setComment(""); setVersion(v => v + 1); onRefresh();
      } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
    }
    async function download(attachment) {
      setBusy(true); setError("");
      try {
        const response = await SDK.authedFetch(API + "/attachments/" + attachment.id + "?board=" + encodeURIComponent(board));
        if (!response.ok) throw new Error(String(response.status));
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a"); link.href = url; link.download = attachment.filename || "результат"; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
      } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
    }
    async function upload(event) {
      const file = event.target.files && event.target.files[0]; if (!file || busy) return;
      setBusy(true); setError("");
      try {
        const form = new FormData(); form.append("file", file);
        const response = await SDK.authedFetch(API + "/tasks/" + encodeURIComponent(taskId) + "/attachments?board=" + encodeURIComponent(board), { method: "POST", body: form });
        if (!response.ok) throw new Error(String(response.status));
        setVersion(v => v + 1);
      } catch (err) { setError(errorText(err)); } finally { setBusy(false); event.target.value = ""; }
    }
    const task = data && data.task;
    const profile = task && profiles.find(p => p.name === task.assignee);
    const summary = task && (task.result || task.latest_summary);
    const blockReason = task && task.status === "blocked" && (task.block_reason || task.latest_summary || task.result);
    return h(Modal, { title: task ? task.title : "Поручение", description: task ? `${statusLabel(task.status)} · ${profile ? profileLabel(profile) : task.assignee || (task.status === "review" ? "Ждёт вашей проверки" : "Исполнитель не назначен")}` : "Загружаем карточку…", busy, onClose },
      error && h("div", { role: "alert", className: "k21-error" }, error, " ", h(Button, { onClick: () => { setError(""); setVersion(v => v + 1); } }, "Повторить")),
      task && h("div", { className: "k21-task-detail" },
        blockReason && h("section", { className: "k21-note" }, h("h3", null, "Что мешает продолжить"), h(RichText, null, blockReason)),
        !blockReason && h("section", { className: "k21-note" }, h("h3", null, ["done", "review"].includes(task.status) ? "Результат" : "Последняя запись агента"), h(RichText, null, summary || "Здесь появится итог работы агента. Он останется в карточке после завершения.")),
        h(["done", "review"].includes(task.status) ? "details" : "section", null,
          h(["done", "review"].includes(task.status) ? "summary" : "h3", null, "Задание"),
          h(RichText, null, task.body || "Описание пока не добавлено. Уточните, какой результат нужен.")),
        h("div", { className: "k21-actions" },
          task.status !== "ready" && task.status !== "running" && h(Button, { primary: task.status !== "review", onClick: () => onMove(task, "ready") }, task.status === "review" ? "На доработку" : "Передать агенту"),
          task.status !== "done" && task.status !== "archived" && h(Button, { primary: task.status === "review", onClick: () => onMove(task, "done") }, completionLabel(task)),
          (task.status === "ready" || task.status === "running") && h(Button, { onClick: () => onMove(task, "blocked") }, "Приостановить"),
          task.status !== "running" && h(Button, { onClick: () => onEdit(task) }, "Изменить задание"),
          task.status !== "archived" && h(Button, { onClick: () => onMove(task, "archived") }, "В архив")),
        h(Button, { onClick: async () => {
          try { await navigator.clipboard.writeText(new URL(taskAddress, window.location.origin).href); setCopied(true); }
          catch (_) { setError("Браузер не разрешил копирование. Скопируйте адрес из адресной строки."); }
        } }, copied ? "Ссылка скопирована" : "Скопировать ссылку на поручение"),
        h("section", null, h("h3", null, "Файлы"),
          (data.attachments || []).map(a => h(Button, { key: a.id, disabled: busy, onClick: () => void download(a) }, a.filename || "Скачать файл")),
          !(data.attachments || []).length && h("p", { className: "k21-muted" }, "Прикрепите исходные данные. Здесь же можно скачать файлы результата."),
          h("input", { ref: uploadInput, type: "file", hidden: true, "aria-label": "Прикрепить файл", disabled: busy, onChange: event => void upload(event) }),
          h(Button, { disabled: busy, onClick: () => uploadInput.current.click() }, "Прикрепить файл")),
        ((data.links || {}).parents || []).length > 0 && h("section", null, h("h3", null, "Сначала должны завершиться"),
          data.links.parents.map(id => h(Button, { key: id, onClick: () => onOpenTask(id) }, "Открыть связанную задачу ", id))),
        (data.child_results || []).length > 0 && h("section", null, h("h3", null, "Связанные поручения"),
          data.child_results.map(child => h(Button, { key: child.id, onClick: () => onOpenTask(child.id) }, child.title, " · ", statusLabel(child.status)))),
        h("section", null, h("h3", null, "Обсуждение"),
          !(data.comments || []).length && h("p", { className: "k21-muted" }, "Можно уточнить условия или оставить обратную связь по результату."),
          (data.comments || []).map(entry => h("article", { key: entry.id, className: "k21-comment" },
            h("small", null, entry.author === "dashboard" ? "Пользователь" : entry.author === "default" ? "Корра" : entry.author || "Агент", " · ", dateLabel(entry.created_at)),
            h(RichText, null, entry.body))),
          h("form", { onSubmit: event => void addComment(event), className: "k21-form" },
            h(Field, { label: "Комментарий" }, h("textarea", { value: comment, onChange: e => setComment(e.target.value), rows: 3, placeholder: "Уточнение или обратная связь" })),
            h("p", { className: "k21-muted" }, "Комментарий сохраняется в задаче. Для возобновления используйте «Передать агенту» или «На доработку»."),
            h(Button, { type: "submit", disabled: busy || !comment.trim() }, busy ? "Сохраняем…" : "Добавить комментарий"))),
        h("details", null, h("summary", null, "История выполнения"),
          !(data.runs || []).length && h("p", null, "Агент ещё не запускался."),
          (data.runs || []).map(run => h("article", { key: run.id, className: "k21-comment" },
            h("strong", null, RUN_LABEL[run.outcome || run.status] || "Попытка завершена"),
            h("small", null, " · ", dateLabel(run.started_at)),
            run.summary && h(RichText, null, run.summary),
            run.error && h("p", null, runErrorText(run.error)))))));
  }

  function KanbanPage() {
    const [params, setParams] = useSearchParams();
    const boardKey = "korra.kanban.selectedBoard:" + useHref("/");
    const [rememberedBoard] = useState(() => { try { return localStorage.getItem(boardKey) || "default"; } catch (_) { return "default"; } });
    const board = params.get("board") || rememberedBoard;
    const taskId = params.get("task");
    const view = params.get("view") || "all";
    function setBoard(value) {
      setModal(null); setSearch(""); setAssignee("");
      setParams(current => { const next = new URLSearchParams(current); next.set("board", value); next.delete("task"); return next; });
    }
    function setView(value) {
      setParams(current => { const next = new URLSearchParams(current); if (value === "all") next.delete("view"); else next.set("view", value); return next; });
    }
    function closeTask() {
      setModal(null);
      setParams(current => { const next = new URLSearchParams(current); next.delete("task"); return next; }, { replace: true });
    }
    const [boards, setBoards] = useState([]);
    const [profiles, setProfiles] = useState([]);
    const [listsError, setListsError] = useState("");
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [search, setSearch] = useState("");
    const [assignee, setAssignee] = useState("");
    const [archived, setArchived] = useState(false);
    const [modal, setModal] = useState(null);
    const [dragged, setDragged] = useState(null);
    const generation = useRef(0);
    const listsGeneration = useRef(0);
    const load = useCallback(async function () {
      const current = ++generation.current;
      try {
        const result = await request("/board?include_archived=" + archived, board);
        if (generation.current === current) { setData(result); setError(""); }
      } catch (err) { if (generation.current === current) setError(errorText(err)); }
      finally { if (generation.current === current) setLoading(false); }
    }, [board, archived]);
    const loadLists = useCallback(async function () {
      const current = ++listsGeneration.current;
      const results = await Promise.allSettled([SDK.fetchJSON(API + "/boards"), SDK.api.getProfiles()]);
      if (current !== listsGeneration.current) return;
      if (results[0].status === "fulfilled") {
        const list = results[0].value.boards || []; setBoards(list);

      }
      if (results[1].status === "fulfilled") setProfiles(results[1].value.profiles || []);
      setListsError(results.some(result => result.status === "rejected") ? "Не удалось обновить список досок или агентов. Уже загруженные данные сохранены." : "");
    }, []);
    useEffect(() => { void loadLists(); return () => { listsGeneration.current++; }; }, [loadLists]);
    useEffect(function () {
      setLoading(true); setData(null); setNotice(""); setError("");
      try { localStorage.setItem(boardKey, board); } catch (_) { /* Хранилище может быть запрещено настройками браузера. */ }
      void load();
      const timer = setInterval(() => { if (document.visibilityState !== "hidden") void load(); }, 10000);
      return function () { clearInterval(timer); generation.current++; };
    }, [board, boardKey, load]);
    const tasks = data ? data.columns.flatMap(column => column.tasks) : [];
    const visible = tasks.filter(task => (view !== "attention" || ["review", "blocked"].includes(task.status)) && (view !== "done" || task.status === "done") && (!assignee || task.assignee === assignee) && (!search || [task.title, task.body, task.tenant, task.id].join(" ").toLocaleLowerCase("ru-RU").includes(search.toLocaleLowerCase("ru-RU"))));
    const columns = ["triage", "todo", "scheduled", ...PRIMARY, "archived"].filter(status => PRIMARY.includes(status) || visible.some(task => task.status === status));
    const meta = boards.find(b => b.slug === board) || { slug: board, name: board === "default" ? "Основная доска" : board };
    const attention = tasks.filter(task => ["review", "blocked"].includes(task.status)).length;
    const activeModal = modal || (taskId ? { kind: "task", id: taskId } : null);
    function saved(message, id) { setModal(null); setNotice(message); if (id) openTask(id); void load(); void loadLists(); }
    function move(task, target) {
      setDragged(null);
      if (!task || task.status === target) return;
      if (!ACTION[target]) { setNotice("Этот этап меняется автоматически. Для запуска передайте задачу в очередь агенту."); return; }
      setModal({ kind: "move", task, target });
    }
    function openTask(id) {
      setModal(null);
      setParams(current => { const next = new URLSearchParams(current); next.set("board", board); next.set("task", id); return next; });
    }
    return h("div", { className: "k21-board", "aria-busy": loading },
      h("header", { className: "k21-board-header" },
        h("div", null, h("h2", null, "От поручения к результату"), h("p", null, "Поставьте задачу агенту, следите за ходом работы и проверяйте итог.")),
        h(Button, { primary: true, onClick: () => setModal({ kind: "create" }), disabled: loading || !data }, "Новое поручение")),
      h("div", { className: "k21-board-controls" },
        h(Field, { label: "Доска" }, h("select", { value: board, onChange: e => { setBoard(e.target.value); setSearch(""); setAssignee(""); }, disabled: !!activeModal },
          !boards.some(b => b.slug === board) && h("option", { value: board }, meta.name),
          boards.map(b => h("option", { key: b.slug, value: b.slug }, b.slug === "default" && (!b.name || b.name === "Default") ? "Основная доска" : b.name || b.slug)))),
        h(Button, { onClick: () => setModal({ kind: "board-new" }) }, "Новая доска"),
        h(Button, { onClick: () => setModal({ kind: "board-settings" }) }, "Настройки доски"),
        h(Button, { onClick: () => { void load(); void loadLists(); } }, "Обновить")),
      meta.description && h("p", { className: "k21-muted" }, meta.description),
      h("div", { className: "k21-board-controls" },
        h(Field, { label: "Найти задачу" }, h("input", { value: search, onChange: e => setSearch(e.target.value), placeholder: "Название, описание или клиент" })),
        h(Field, { label: "Исполнитель" }, h("select", { value: assignee, onChange: e => setAssignee(e.target.value) },
          h("option", { value: "" }, "Все агенты"),
          Array.from(new Set([...profiles.map(p => p.name), ...tasks.map(t => t.assignee).filter(Boolean)])).map(name => h("option", { key: name, value: name }, profileLabel(profiles.find(p => p.name === name) || { name }))))),
        h("label", { className: "k21-checkbox" }, h("input", { type: "checkbox", checked: archived, onChange: e => setArchived(e.target.checked) }), "Показать архив"),
        (search || assignee || view !== "all") && h(Button, { onClick: () => { setSearch(""); setAssignee(""); setView("all"); } }, "Сбросить фильтры")),
      h("div", { className: "k21-view-tabs", "aria-label": "Какие поручения показать" },
        [["all", "Все поручения"], ["attention", "Нужно ваше внимание · " + attention], ["done", "Результаты"]].map(([value, label]) =>
          h(Button, { key: value, "aria-pressed": view === value, onClick: () => setView(value) }, label))),
      listsError && h("div", { role: "alert", className: "k21-error" }, listsError, " ", h(Button, { onClick: () => void loadLists() }, "Повторить загрузку списков")),
      h("p", { className: "k21-muted" }, attention ? `Нужно ваше внимание: ${attention}. Откройте карточки в колонках «Нужно решение» и «На проверке».` : "Перетаскивайте карточки между этапами или откройте поручение и выберите действие. Доска обновляется автоматически."),
      notice && h("div", { role: "status", className: "k21-note" }, notice),
      error && h("div", { role: "alert", className: "k21-error" }, error, " ", h(Button, { onClick: () => void load() }, "Повторить"), board !== "default" && h(Button, { onClick: () => setBoard("default") }, "На основную доску")),
      loading && h("p", { role: "status" }, "Загружаем доску…"),
      !loading && data && !tasks.length && h("section", { className: "k21-empty" },
        h("h3", null, "Начните с одного понятного поручения"),
        h("p", null, "Например: сравнить поставщиков, подготовить ответы клиентам или составить план продаж. Укажите, какой результат вы хотите получить."),
        h(Button, { primary: true, onClick: () => setModal({ kind: "create" }) }, "Поручить первую задачу")),
      !loading && tasks.length > 0 && visible.length === 0 && h("p", { role: "status" }, "По этим фильтрам ничего не найдено. Измените запрос или сбросьте фильтры."),
      data && h("div", { className: "k21-board-scroll", tabIndex: 0, "aria-label": "Колонки доски; прокрутка по горизонтали" },
        h("div", { className: "k21-columns", style: { "--k21-columns": columns.length } }, columns.map(status => {
          const cards = visible.filter(task => task.status === status);
          return h("section", { key: status, className: "k21-column", "data-status": status,
            onDragOver: e => { if (ACTION[status]) e.preventDefault(); },
            onDrop: e => { e.preventDefault(); const id = e.dataTransfer.getData("text/x-korra-task"); move(tasks.find(task => task.id === id), status); },
            "data-drop-available": dragged && ACTION[status] ? "true" : undefined },
            h("div", { className: "k21-column-title" }, h("h3", null, statusLabel(status)), h("span", null, cards.length)),
            h("p", { className: "k21-column-hint" }, STATUS[status][1]),
            !cards.length && h("p", { className: "k21-column-empty" }, "Пока нет задач"),
            cards.map(task => {
              const profile = profiles.find(p => p.name === task.assignee);
              const preview = ["blocked", "review", "done"].includes(task.status) ? task.block_reason || task.latest_summary || task.result || task.body : task.body;
              return h("button", { key: task.id, type: "button", className: "k21-task", draggable: true, "data-task-id": task.id,
                onClick: () => openTask(task.id),
                onDragStart: e => { e.dataTransfer.setData("text/x-korra-task", task.id); e.dataTransfer.effectAllowed = "move"; setDragged(task.id); },
                onDragEnd: () => setDragged(null), "aria-label": "Открыть поручение: " + task.title },
                h("strong", null, task.title),
                task.tenant && h("span", { className: "k21-task-tag" }, task.tenant),
                preview && h("p", { className: "k21-task-preview" }, preview),
                h("span", { className: "k21-task-agent" }, profile ? profileLabel(profile) : task.assignee || (task.status === "review" ? "Ждёт вашей проверки" : task.status === "done" ? "Результат сохранён" : "Назначьте исполнителя")),
                h("span", { className: "k21-task-meta" }, task.priority > 0 ? "Приоритет: " + task.priority + " · " : "", "Создано ", dateLabel(task.created_at)),
                task.progress && h("span", { className: "k21-task-meta" }, `Подзадачи: ${task.progress.done} из ${task.progress.total}`),
                task.comment_count > 0 && h("span", { className: "k21-task-meta" }, "Комментариев: ", task.comment_count));
            }));
        }))),
      activeModal && activeModal.kind === "create" && h(TaskForm, { board, profiles, boardMeta: meta, onClose: () => setModal(null), onSaved: saved }),
      activeModal && activeModal.kind === "edit" && h(TaskForm, { board, profiles, task: activeModal.task, onClose: () => openTask(activeModal.task.id), onSaved: saved }),
      activeModal && activeModal.kind === "move" && h(MoveDialog, { board, profiles, task: activeModal.task, target: activeModal.target, onClose: () => setModal(null), onSaved: saved }),
      activeModal && activeModal.kind.startsWith("board-") && h(BoardSettings, { board: meta, creating: activeModal.kind === "board-new", onClose: () => setModal(null), onSaved: next => { setBoards(list => [...list.filter(item => item.slug !== next.slug), next]); setBoard(next.slug); setModal(null); void loadLists(); } }),
      activeModal && activeModal.kind === "task" && h(TaskDetail, { key: activeModal.id, taskId: activeModal.id, board, profiles, onClose: closeTask, onRefresh: () => void load(), onMove: move, onEdit: task => setModal({ kind: "edit", task }), onOpenTask: openTask }));
  }
  function KanbanEntry() {
    return h("section", { className: "k21-note k21-board-header" },
      h("div", null, h("h2", null, "Разовые поручения"),
        h("p", null, "Поручите агенту конкретную работу и получите результат на доске. Ниже — задачи по расписанию.")),
      h(Link, { to: "/kanban", className: "neo-button" }, "Открыть доску"));
  }
  window.__HERMES_PLUGINS__.registerSlot("kanban", "cron:top", KanbanEntry);
  window.__HERMES_PLUGINS__.register("kanban", KanbanPage);
})();
