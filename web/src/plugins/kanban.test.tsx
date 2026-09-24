// @vitest-environment jsdom
import React, { act } from "react";
import * as router from "react-router";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let root: Root;
let host: HTMLDivElement;
const fetchJSON = vi.fn();
const profiles = vi.fn();
const authedFetch = vi.fn();
const columns = ["ready", "running", "blocked", "review", "done"];
const task = { id: "task-1", title: "Сравнить поставщиков", body: "Собрать таблицу условий", status: "ready", assignee: "default" };
let boardTasks: Array<typeof task & Record<string, unknown>>;
let attentionPayload: { items: Array<Record<string, unknown>>; count: number; errors: unknown[] };
let taskExtras: Record<string, unknown>;
function Children({ children }: { children?: React.ReactNode }) { return <>{children}</>; }
function Content({ children }: { children?: React.ReactNode }) { return <div role="dialog">{children}</div>; }
async function renderPage(path = "/kanban") {
  let Page: React.ComponentType | undefined;
  Object.assign(window, {
    __HERMES_PLUGIN_SDK__: { React, router, fetchJSON, authedFetch, api: { getProfiles: profiles }, components: { Dialog: Children, DialogContent: Content, DialogTitle: Children, DialogDescription: Children } },
    __HERMES_PLUGINS__: { registerSlot: vi.fn(), register: (_name: string, component: React.ComponentType) => { Page = component; } },
  });
  // @ts-expect-error Плагин исполняется браузером напрямую, без сборщика TypeScript.
  await import("../../../plugins/kanban/dashboard/dist/index.js");
  const Component = Page!;
  await act(async () => root.render(<router.MemoryRouter initialEntries={[path]}><Component /></router.MemoryRouter>));
}
function button(name: string) {
  const found = Array.from(host.querySelectorAll("button")).find(el => el.textContent === name);
  if (!found) throw new Error(`Кнопка не найдена: ${name}`);
  return found;
}
async function click(element: HTMLElement) { await act(async () => element.click()); }
async function change(element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), "value")!.set!.call(element, value);
    element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
function field(label: string) {
  const found = Array.from(host.querySelectorAll("label")).find(el => el.textContent === label);
  return document.getElementById(found!.htmlFor) as HTMLInputElement;
}
const writes = () => fetchJSON.mock.calls.filter(([, options]) => options?.method);

beforeEach(() => {
  vi.resetModules(); localStorage.clear(); boardTasks = [];
  attentionPayload = { items: [], count: 0, errors: [] }; taskExtras = {};
  authedFetch.mockReset().mockResolvedValue(new Response(""));
  profiles.mockReset().mockResolvedValue({ profiles: [{ name: "default", is_default: true }] });
  fetchJSON.mockReset().mockImplementation(async (url: string, options?: { method: string }) => {
    if (options?.method) return {};
    if (url.endsWith("/boards")) return { boards: [{ slug: "default", name: "Default" }] };
    if (url.endsWith("/attention")) return attentionPayload;
    if (url.includes("/board?")) return { columns: columns.map(name => ({ name, tasks: boardTasks.filter(t => t.status === name) })) };
    if (url.includes("/tasks/task-1")) return { task: boardTasks[0] || task, comments: [], attachments: [], links: {}, runs: [], ...taskExtras };
    throw new Error("Неожиданный запрос " + url);
  });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

describe("Доска поручений", () => {
  it("требует результат и исполнителя перед созданием и явно сообщает о запуске", async () => {
    await renderPage(); expect(host.textContent).toContain("Начните с одного понятного поручения");
    await click(button("Новое поручение")); expect(button("Передать агенту").hasAttribute("disabled")).toBe(true);
    await change(field("Что нужно сделать"), "Подготовить сводку");
    await change(field("Задание и ожидаемый результат"), "Таблица по трём поставщикам");
    await change(field("Кому поручить"), "default"); await click(button("Передать агенту"));
    const [url, options] = writes()[0]; expect(url).toContain("board=default");
    expect(JSON.parse(options.body)).toMatchObject({ title: "Подготовить сводку", body: "Таблица по трём поставщикам", assignee: "default" });
    expect(host.textContent).toContain("может начать его автоматически");
  });
  it("при ошибке сохраняет введённое и повторяет запрос с тем же ключом", async () => {
    await renderPage(); await click(button("Новое поручение"));
    await change(field("Что нужно сделать"), "Сводка");
    await change(field("Задание и ожидаемый результат"), "Таблица");
    await change(field("Кому поручить"), "default");
    fetchJSON.mockRejectedValueOnce(new Error("503: traceback /private")); await click(button("Передать агенту"));
    expect(field("Что нужно сделать").value).toBe("Сводка"); expect(host.textContent).not.toContain("traceback");
    await click(button("Передать агенту"));
    const requests = writes().map(([, options]) => JSON.parse(options.body));
    expect(requests[0].idempotency_key).toBe(requests[1].idempotency_key);
  });
  it("перетаскивание открывает подтверждение, отмена не меняет задачу", async () => {
    boardTasks = [task]; await renderPage();
    await act(async () => {
      const event = new Event("drop", { bubbles: true });
      Object.defineProperty(event, "dataTransfer", { value: { getData: () => task.id } });
      host.querySelector('[data-status="done"]')!.dispatchEvent(event);
    });
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("Что получилось");
    expect(writes()).toHaveLength(0); await click(button("Отмена")); expect(writes()).toHaveLength(0);
    expect(host.querySelector('[data-status="ready"] [data-task-id]')).not.toBeNull();
  });
  it("завершает задачу только с сохранённым итогом", async () => {
    boardTasks = [task]; await renderPage(); await click(host.querySelector('[data-task-id]')!);
    await click(button("Завершить поручение")); expect(button("Завершить поручение").hasAttribute("disabled")).toBe(true);
    await change(field("Что получилось"), "Выбран поставщик А: доставляет за два дня.");
    await click(button("Завершить поручение")); const payload = JSON.parse(writes()[0][1].body);
    expect(payload.status).toBe("done"); expect(payload.summary).toBe(payload.result); expect(payload.result).toContain("поставщик А");
  });
  it("пауза из карточки показывает исход в снова открытой карточке, а не под ней", async () => {
    boardTasks = [task];
    const base = fetchJSON.getMockImplementation()!;
    fetchJSON.mockImplementation(async (url: string, options?: { method: string }) =>
      options?.method === "PATCH" ? { warning: "worker_stop_unconfirmed" } : base(url, options));
    await renderPage(); await click(host.querySelector('[data-task-id]')!);
    await click(button("Приостановить")); await click(button("Приостановить"));
    expect(JSON.parse(writes()[0][1].body)).toMatchObject({ status: "blocked" });
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("остановку агента подтвердить не удалось");
    await click(button("Закрыть")); expect(host.textContent).toContain("остановку агента подтвердить не удалось");
    await click(host.querySelector('[data-task-id]')!);
    expect(host.querySelector('[role="dialog"]')?.textContent).not.toContain("остановку агента");
  });
  it("называет причину остановки и не выдаёт её за готовый результат", async () => {
    boardTasks = [{ ...task, status: "blocked", latest_summary: "Нужно согласовать бюджет" }];
    await renderPage(); await click(host.querySelector('[data-task-id]')!);
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("Вопрос агента");
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("Нужно согласовать бюджет");
    await click(button("Завершить поручение"));
    expect(field("Что получилось").value).toBe("");
    expect(button("Завершить поручение").hasAttribute("disabled")).toBe(true);
  });
  it("фильтрует карточки без изменения данных и возвращает весь список", async () => {
    boardTasks = [task]; await renderPage(); await change(field("Найти задачу"), "отсутствует");
    expect(host.querySelectorAll("[data-task-id]")).toHaveLength(0); expect(host.textContent).toContain("По этим фильтрам ничего не найдено");
    await click(button("Сбросить фильтры")); expect(host.querySelectorAll("[data-task-id]")).toHaveLength(1); expect(writes()).toHaveLength(0);
  });
  it("открывает адресное поручение на нужной доске и сохраняет полный результат при приёмке", async () => {
    boardTasks = [{ ...task, status: "review", latest_summary: "Короткое резюме", result: "Полная таблица: А — 10 дней, Б — 2 дня. Выбран Б." }];
    await renderPage("/kanban?board=sales&task=task-1");
    expect(fetchJSON.mock.calls.some(([url]) => url.includes("/tasks/task-1?board=sales"))).toBe(true);
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("Полная таблица");
    expect(host.querySelector('[role="dialog"] details summary')?.textContent).toBe("Задание");
    await click(button("Принять результат"));
    expect(field("Что получилось").value).toBe(boardTasks[0].result);
    await click(button("Принять результат"));
    expect(JSON.parse(writes()[0][1].body).result).toBe(boardTasks[0].result);
  });
  it("показывает результаты отдельным представлением и сбрасывает фильтр без записи", async () => {
    boardTasks = [task, { ...task, id: "task-2", status: "done" }];
    await renderPage("/kanban?view=done");
    expect(Array.from(host.querySelectorAll('[data-task-id]')).map(el => el.getAttribute("data-task-id"))).toEqual(["task-2"]);
    await click(button("Сбросить фильтры"));
    expect(host.querySelectorAll('[data-task-id]')).toHaveLength(2);
    expect(writes()).toHaveLength(0);
  });
  it("передаёт исполнителю поручение только после загрузки всех исходных файлов и безопасно повторяет загрузку", async () => {
    await renderPage(); await click(button("Новое поручение"));
    await change(field("Что нужно сделать"), "Сравнить цены из файлов");
    await change(field("Задание и ожидаемый результат"), "Выбрать поставщика");
    await change(field("Кому поручить"), "default");
    await act(async () => {
      const input = host.querySelector('input[aria-label="Исходные файлы"]')!;
      Object.defineProperty(input, "files", { value: [new File(["Цены"], "цены.txt"), new File(["Сроки"], "сроки.txt")] });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    fetchJSON.mockResolvedValueOnce({ task: { ...task, assignee: null } });
    authedFetch.mockResolvedValueOnce(new Response("")).mockResolvedValueOnce(new Response("", { status: 503 }));
    await click(button("Передать агенту"));
    expect(JSON.parse(writes()[0][1].body).assignee).toBeNull();
    expect(writes()).toHaveLength(1);
    expect(host.textContent).toContain("Поручение сохранено без запуска");
    expect(field("Что нужно сделать").value).toBe("Сравнить цены из файлов");
    const uploadCountAtAssign: number[] = [];
    const previous = fetchJSON.getMockImplementation()!;
    fetchJSON.mockImplementation(async (...args) => {
      const [, options] = args;
      if (options?.method === "PATCH" && JSON.parse(options.body).assignee === "default") uploadCountAtAssign.push(authedFetch.mock.calls.length);
      return previous(...args);
    });
    await click(button("Передать агенту"));
    expect(authedFetch).toHaveBeenCalledTimes(3);
    expect(uploadCountAtAssign).toEqual([3]);
    expect(writes().filter(([, options]) => options.method === "POST")).toHaveLength(1);
    expect(writes().every(([url]) => url.includes("board=default"))).toBe(true);
  });

  it("ответ агенту сохраняется одной операцией и повторяется с тем же ключом", async () => {
    boardTasks = [{ ...task, status: "blocked", owner_attention: "question", block_reason: "Подтвердите 4 замены", block_revision: 41 }];
    await renderPage(); await click(host.querySelector('[data-task-id]')!);
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("Подтвердите 4 замены");
    expect(button("Ответить и продолжить").hasAttribute("disabled")).toBe(true);
    await change(field("Ваш ответ"), "Подтверждаю, кроме каталога");
    fetchJSON.mockRejectedValueOnce(new Error("503: сбой")); await click(button("Ответить и продолжить"));
    await click(button("Ответить и продолжить"));
    const posts = writes().filter(([url]) => url.includes("/tasks/task-1/respond"));
    expect(posts).toHaveLength(2);
    const [first, second] = posts.map(([, options]) => JSON.parse(options.body));
    expect(first).toMatchObject({ answer: "Подтверждаю, кроме каталога", revision: 41 });
    expect(second.request_id).toBe(first.request_id);
    expect(writes().some(([url]) => url.includes("/comments"))).toBe(false);
  });
  it("объясняет, что агент уже задал новый вопрос", async () => {
    boardTasks = [{ ...task, status: "blocked", owner_attention: "question", block_reason: "Старый вопрос", block_revision: 7 }];
    await renderPage(); await click(host.querySelector('[data-task-id]')!);
    fetchJSON.mockRejectedValueOnce(Object.assign(new Error("409: Данные уже изменились."), { status: 409, payload: { detail: { code: "question_changed" } } }));
    await click(button("Продолжить как предложено"));
    expect(host.textContent).toContain("Агент уже задал новый вопрос");
  });
  it("принимает ровно ту версию результата, которую видит владелец, и возвращает только с замечанием", async () => {
    boardTasks = [{ ...task, status: "review", acceptance: "owner", owner_attention: "accept", submitted_version: 99, result: "1. Поручения — знакомое слово" }];
    await renderPage(); await click(host.querySelector('[data-task-id]')!);
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("Результат ждёт вашей проверки");
    await click(button("Вернуть с замечанием"));
    expect(button("Вернуть агенту").hasAttribute("disabled")).toBe(true);
    await change(field("Что исправить"), "Добавь четвёртый вариант");
    await click(button("Вернуть агенту"));
    const [url, options] = writes()[0];
    expect(url).toContain("/tasks/task-1/request-changes");
    expect(JSON.parse(options.body)).toMatchObject({ version: 99, comment: "Добавь четвёртый вариант" });
  });
  it("кнопка «Принять» отправляет приёмку с версией", async () => {
    boardTasks = [{ ...task, status: "review", acceptance: "owner", owner_attention: "accept", submitted_version: 5, result: "Итог" }];
    await renderPage(); await click(host.querySelector('[data-task-id]')!);
    await click(button("Принять"));
    const [url, options] = writes()[0];
    expect(url).toContain("/tasks/task-1/accept"); expect(JSON.parse(options.body).version).toBe(5);
  });
  it("показывает подробности результата, если агент положил их в metadata", async () => {
    boardTasks = [{ ...task, status: "done", latest_summary: "Три варианта" }];
    taskExtras = { runs: [{ id: 1, outcome: "completed", summary: "Три варианта", metadata: { options: [{ name: "Поручения", rationale: "Знакомое деловое слово" }], worker_session_id: "s1" } }] };
    await renderPage(); await click(host.querySelector('[data-task-id]')!);
    const text = host.querySelector('[role="dialog"]')?.textContent || "";
    expect(text).toContain("Подробности от агента");
    expect(text).toContain("Поручения — Знакомое деловое слово");
    expect(text).not.toContain("worker_session_id");
  });
  it("«Ждёт вас» собирает вопросы со всех досок и открывает карточку на её доске", async () => {
    attentionPayload = { count: 1, errors: [], items: [{ board: "sales", board_name: "Продажи", task_id: "task-1", title: "Заменить ссылки", kind: "question", question: "Подтвердите замены", plan_title: "Новая система ботов", assignee: "default" }] };
    await renderPage();
    expect(host.textContent).toContain("Ждёт вас · 1");
    expect(host.textContent).toContain("План «Новая система ботов»");
    await click(button("Ответить"));
    expect(fetchJSON.mock.calls.some(([url]) => url.includes("/tasks/task-1?board=sales"))).toBe(true);
  });
  it("собственная пауза владельца не попадает в «Ждёт вас» и не расходится со счётчиком", async () => {
    attentionPayload = { count: 0, errors: [], items: [{ board: "default", task_id: "task-1", title: "Сравнить поставщиков", kind: "paused", assignee: "default" }] };
    boardTasks = [{ ...task, status: "blocked", block_kind: "paused", owner_attention: "paused" }];
    await renderPage();
    expect(host.querySelector('section[aria-label="Ждёт вас"]')).toBeNull();
    expect(host.querySelector('[data-task-id="task-1"]')?.textContent).toContain("На паузе");
    await click(button("Ждёт вас · 0"));
    expect(host.querySelector('[data-task-id="task-1"]')).toBeNull();
  });
  it("шаг владельца не отдаётся агенту и отмечается выполненным самим владельцем", async () => {
    boardTasks = [{ ...task, assignee: null as unknown as string, actor_kind: "human", owner_attention: "human_step" }];
    await renderPage();
    expect(host.querySelector('[data-task-id]')?.textContent).toContain("Ваш шаг");
    await click(host.querySelector('[data-task-id]')!);
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("Это ваш шаг");
    expect(Array.from(host.querySelectorAll("button")).some(el => el.textContent === "Передать агенту")).toBe(false);
    await click(button("Отметить выполненным"));
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("Что получилось");
  });
  it("по умолчанию просит прислать результат владельцу на проверку", async () => {
    await renderPage(); await click(button("Новое поручение"));
    await change(field("Что нужно сделать"), "Сводка");
    await change(field("Задание и ожидаемый результат"), "Таблица");
    await change(field("Кому поручить"), "default"); await click(button("Передать агенту"));
    expect(JSON.parse(writes()[0][1].body).acceptance).toBe("owner");
  });

  it("разрешение на внешние изменения даётся только явным решением владельца", async () => {
    boardTasks = [{ ...task, status: "blocked", owner_attention: "question", needs_approval: true, block_kind: "approval", block_reason: "Разрешите 4 замены в CRM", block_revision: 12 }];
    await renderPage(); await click(host.querySelector('[data-task-id]')!);
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("Нужно ваше разрешение");
    expect(Array.from(host.querySelectorAll("button")).some(el => el.textContent === "Ответить и продолжить")).toBe(false);
    await click(button("Не разрешать"));
    const [url, options] = writes()[0];
    expect(url).toContain("/tasks/task-1/respond");
    expect(JSON.parse(options.body)).toMatchObject({ decision: "deny", revision: 12 });
  });
});
