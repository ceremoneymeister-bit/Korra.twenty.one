// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n";

vi.mock("@/contexts/usePageHeader", () => ({ usePageHeader: () => ({ setAfterTitle: () => {} }) }));
vi.mock("@/components/ProfileScopeChip", () => ({ ProfileScopeChip: () => null }));
vi.mock("@/components/KorraLoader", () => ({ KorraLoader: () => <p>Загрузка</p> }));

import { api, type EnvVarInfo, type GoogleWorkspaceStatus, type OAuthProvider } from "@/lib/api";
import EnvPage from "./EnvPage";

let root: Root;
let host: HTMLDivElement;
const info = (isSet: boolean, label: string, advanced = false): EnvVarInfo => ({
  is_set: isSet, redacted_value: isSet ? "***" : null, description: "Ключ сервиса",
  url: null, category: "provider", is_password: true, tools: [], advanced, provider_label: label,
});
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.spyOn(api, "getEnvVars").mockResolvedValue({
    WORKING_API_KEY: info(true, "Сервис команды", true),
    NEW_API_KEY: info(false, "Новый сервис"),
  });
  vi.spyOn(api, "getOAuthProviders").mockResolvedValue({ providers: [{
    id: "qwen-oauth", name: "Qwen", flow: "external", cli_command: "hermes auth add qwen-oauth",
    status: { logged_in: false },
  } as OAuthProvider] });
  // Карточка Google опрашивает статус при монтировании страницы. Без ответа
  // `act` не досчитывается до конца, и каталог падает по таймауту, ничего не
  // сказав про сам каталог.
  vi.spyOn(api, "getGoogleWorkspaceStatus").mockResolvedValue({
    app: { configured: false },
    connection: { state: "not_connected" },
    pending: { active: false },
    available_services: [],
    completion_mode: "manual_localhost_url",
  } satisfies GoogleWorkspaceStatus);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); });

function button(name: string) {
  return Array.from(host.querySelectorAll("button")).find(button => button.textContent === name)!;
}
const click = async (element: HTMLElement) => { await act(async () => element.click()); };

describe("Подключения без перегруженного каталога", () => {
  it("сначала показывает настроенный сервис, включая расширенные ключи; остальные доступны через поиск", async () => {
    await act(async () => root.render(<MemoryRouter><I18nProvider><EnvPage /></I18nProvider></MemoryRouter>));
    expect(host.textContent).toContain("Сервис команды");
    expect(host.textContent).not.toContain("Новый сервис");
    await click(button("Подключить сервис"));
    expect(host.textContent).toContain("Новый сервис");
    await act(async () => {
      const input = host.querySelector('input[aria-label="Найти сервис"]')!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "NEW_API_KEY");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(host.textContent).toContain("Новый сервис");
    expect(host.textContent).not.toContain("Сервис команды");
    await click(button("Показать подключённые"));
    expect(host.textContent).toContain("Сервис команды");
  });
  it("сохраняет доступ к входу через аккаунт и правильной команде терминала", async () => {
    await act(async () => root.render(<MemoryRouter><I18nProvider><EnvPage /></I18nProvider></MemoryRouter>));
    expect(host.querySelector("code")?.textContent ?? "").not.toContain("auth add");
    await click(button("Подключить аккаунт"));
    expect(host.querySelector("details code")?.textContent).toBe("korra auth add qwen-oauth");
    expect(host.querySelector("details summary")?.textContent).toBe("Подключение через терминал");
    expect(host.textContent).toContain("Статус входа не гарантирует доступность сервиса");
  });
});
