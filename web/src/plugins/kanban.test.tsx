// @vitest-environment jsdom
import React, { act } from "react";
import * as router from "react-router";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let root: Root;
let host: HTMLDivElement;
const fetchJSON = vi.fn();
const profiles = vi.fn();
const columns = ["ready", "running", "blocked", "review", "done"];
const task = { id: "task-1", title: "Сравнить поставщиков", body: "Собрать таблицу условий", status: "ready", assignee: "default" };
let boardTasks: Array<typeof task & { latest_summary?: string }>;
function Children({ children }: { children?: React.ReactNode }) { return <>{children}</>; }
function Content({ children }: { children?: React.ReactNode }) { return <div role="dialog">{children}</div>; }
async function renderPage() {
  let Page: React.ComponentType | undefined;
  Object.assign(window, {
    __HERMES_PLUGIN_SDK__: { React, router, fetchJSON, api: { getProfiles: profiles }, components: { Dialog: Children, DialogContent: Content, DialogTitle: Children, DialogDescription: Children } },
    __HERMES_PLUGINS__: { registerSlot: vi.fn(), register: (_name: string, component: React.ComponentType) => { Page = component; } },
  });
  // @ts-expect-error Плагин исполняется браузером напрямую, без сборщика TypeScript.
  await import("../../../plugins/kanban/dashboard/dist/index.js");
  const Component = Page!;
  await act(async () => root.render(<router.MemoryRouter><Component /></router.MemoryRouter>));
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
  profiles.mockReset().mockResolvedValue({ profiles: [{ name: "default", is_default: true }] });
  fetchJSON.mockReset().mockImplementation(async (url: string, options?: { method: string }) => {
    if (options?.method) return {};
    if (url.endsWith("/boards")) return { boards: [{ slug: "default", name: "Default" }] };
    if (url.includes("/board?")) return { columns: columns.map(name => ({ name, tasks: boardTasks.filter(t => t.status === name) })) };
    if (url.includes("/tasks/task-1")) return { task: boardTasks[0] || task, comments: [], attachments: [], links: {}, runs: [] };
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
    await click(button("Принять результат")); expect(button("Принять результат").hasAttribute("disabled")).toBe(true);
    await change(field("Что получилось"), "Выбран поставщик А: доставляет за два дня.");
    await click(button("Принять результат")); const payload = JSON.parse(writes()[0][1].body);
    expect(payload.status).toBe("done"); expect(payload.summary).toBe(payload.result); expect(payload.result).toContain("поставщик А");
  });
  it("называет причину остановки и не выдаёт её за готовый результат", async () => {
    boardTasks = [{ ...task, status: "blocked", latest_summary: "Нужно согласовать бюджет" }];
    await renderPage(); await click(host.querySelector('[data-task-id]')!);
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("Что мешает продолжить");
    await click(button("Принять результат"));
    expect(field("Что получилось").value).toBe("");
    expect(button("Принять результат").hasAttribute("disabled")).toBe(true);
  });
  it("фильтрует карточки без изменения данных и возвращает весь список", async () => {
    boardTasks = [task]; await renderPage(); await change(field("Найти задачу"), "отсутствует");
    expect(host.querySelectorAll("[data-task-id]")).toHaveLength(0); expect(host.textContent).toContain("По этим фильтрам ничего не найдено");
    await click(button("Сбросить фильтры")); expect(host.querySelectorAll("[data-task-id]")).toHaveLength(1); expect(writes()).toHaveLength(0);
  });
});
