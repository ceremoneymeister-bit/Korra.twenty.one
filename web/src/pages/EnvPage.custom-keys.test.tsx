// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n";

vi.mock("@/contexts/usePageHeader", () => ({ usePageHeader: () => ({ setAfterTitle: () => {} }) }));
vi.mock("@/components/ProfileScopeChip", () => ({ ProfileScopeChip: () => null }));
vi.mock("@/components/KorraLoader", () => ({ KorraLoader: () => <p>Загрузка</p> }));

import { api, type EnvVarInfo, type GoogleWorkspaceStatus } from "@/lib/api";
import EnvPage from "./EnvPage";

let root: Root;
let host: HTMLDivElement;
let rows: Record<string, EnvVarInfo>;
const key = "MY_BOARD_API_KEY";
const secret = "synthetic-secret-for-owner-test";

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  window.__KORRA_UI_MODE__ = "fleet";
  rows = {};
  vi.spyOn(api, "getEnvVars").mockImplementation(async () => ({ ...rows }));
  vi.spyOn(api, "setEnvVar").mockImplementation(async (name, value) => {
    rows[name] = { is_set: true, redacted_value: value.slice(0, 4) + "..." + value.slice(-4),
      description: "", url: null, category: "custom", is_password: true, tools: [], advanced: false, custom: true };
    return { ok: true };
  });
  vi.spyOn(api, "getOAuthProviders").mockResolvedValue({ providers: [] });
  vi.spyOn(api, "getConnections").mockResolvedValue({ google: { app: { configured: false }, profiles: [], available_services: [] } });
  vi.spyOn(api, "getICloudCalendarStatus").mockResolvedValue({ state: "not_connected", account: null });
  vi.spyOn(api, "getGoogleWorkspaceStatus").mockResolvedValue({
    app: { configured: false }, connection: { state: "not_connected" }, pending: { active: false },
    available_services: [], completion_mode: "manual_localhost_url",
  } satisfies GoogleWorkspaceStatus);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); delete window.__KORRA_UI_MODE__;
});
const render = async () => act(async () => root.render(<MemoryRouter><I18nProvider><EnvPage /></I18nProvider></MemoryRouter>));
const click = async (element: HTMLElement) => act(async () => element.click());
const fill = async (input: HTMLInputElement, value: string) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
});
const card = () => {
  const element = host.querySelector<HTMLElement>("#section-custom");
  expect(element, "Владелец должен иметь доступ к добавлению ключа в продуктовом режиме").not.toBeNull();
  return element!;
};
const button = (text: string) => [...card().querySelectorAll("button")].find(el => el.textContent === text)!;
async function addKey() {
  await fill(card().querySelector<HTMLInputElement>('input[aria-label="Имя переменной"]')!, "  my_board_api_key  ");
  await click(button("Добавить"));
}

describe("Пользовательские ключи в интерфейсе владельца", () => {
  it("добавляет ключ вне каталога, скрывает ввод и сохраняет его после повторного открытия", async () => {
    await render(); await addKey();
    const input = card().querySelector<HTMLInputElement>('input[type="password"]');
    expect(input).not.toBeNull();
    await fill(input!, secret); await click(button("Сохранить"));
    expect(api.setEnvVar).toHaveBeenCalledWith(key, secret);
    expect(card().textContent).toContain(key);
    expect(card().textContent).toContain("Задано");
    expect(card().textContent).not.toContain(secret);
    expect(card().querySelector('input[type="password"]')).toBeNull();
    await act(async () => root.render(null)); await render();
    expect(card().textContent).toContain(key);
    expect(card().textContent).toContain("Задано");
    expect(card().textContent).not.toContain(secret);
  });
  it("не добавляет недопустимое имя и не отправляет его на сервер", async () => {
    await render();
    await fill(card().querySelector<HTMLInputElement>('input[aria-label="Имя переменной"]')!, "not a key");
    expect(button("Добавить").disabled).toBe(true);
    expect(api.setEnvVar).not.toHaveBeenCalled();
  });
  it("при отказе сохранения оставляет скрытый ввод для исправления и не показывает успех", async () => {
    vi.mocked(api.setEnvVar).mockRejectedValueOnce(new Error("Сохранение недоступно"));
    await render(); await addKey();
    await fill(card().querySelector<HTMLInputElement>('input[type="password"]')!, secret);
    await click(button("Сохранить"));
    expect(card().querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe(secret);
    expect(card().textContent).toContain("Задано пользовательских ключей: 0");
    expect(card().textContent).toContain("Не задано");
    expect(rows[key]).toBeUndefined();
    expect(host.textContent).not.toContain(`Ключ ${key} сохранён`);
    expect(host.textContent).not.toContain(secret);
  });
});
