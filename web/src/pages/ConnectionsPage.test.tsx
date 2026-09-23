// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConnectionsGoogleProfile, ConnectionsResponse, GoogleWorkspaceStatus } from "@/lib/api";
import ConnectionsPage from "./ConnectionsPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// KorraLoader оборачивает `window.matchMedia` при импорте, а в jsdom его нет.
vi.mock("@/components/KorraLoader", () => ({ KorraLoader: () => <p>Загрузка</p> }));

const api = vi.hoisted(() => ({
  getConnections: vi.fn(),
  setGoogleWorkspaceSharing: vi.fn(),
  revokeGoogleWorkspace: vi.fn(),
  checkGoogleWorkspaceService: vi.fn(),
  getGoogleWorkspaceStatus: vi.fn(),
  startGoogleWorkspace: vi.fn(),
  completeGoogleWorkspace: vi.fn(),
  cancelGoogleWorkspace: vi.fn(),
}));
vi.mock(import("@/lib/api"), async (importOriginal) => ({
  ...(await importOriginal()),
  api: api as unknown as typeof import("@/lib/api").api,
}));

function row(profile: string, over: Partial<ConnectionsGoogleProfile> = {}): ConnectionsGoogleProfile {
  return {
    profile,
    label: "",
    access: "none",
    state: "not_connected",
    services: [],
    pending: false,
    tools: { calendar: true, workspace_skill: true },
    ...over,
  };
}

function connections(rows: ConnectionsGoogleProfile[], configured = true): ConnectionsResponse {
  return { google: { app: { configured }, profiles: rows, available_services: ["email", "calendar", "drive"] } };
}

const MAIN = row("default", {
  access: "own",
  state: "connected",
  services: ["calendar", "drive"],
  shared_with: ["designer"],
});
const DESIGNER = row("designer", {
  label: "Дизайнер",
  access: "shared",
  state: "connected",
  services: ["calendar", "drive"],
  shared_from: "default",
  tools: { calendar: true, workspace_skill: false },
});
const LAWYER = row("lawyer", { label: "Юрист" });
const SMM = row("smm", { label: "SMM", access: "own", state: "connected", services: ["email"] });

function status(over: Partial<GoogleWorkspaceStatus["connection"]> = {}): GoogleWorkspaceStatus {
  return {
    app: { configured: true, credential_type: "installed", redirect_uri: "http://localhost" },
    connection: { state: "not_connected", services: [], action: "connect", ...over },
    pending: { active: false },
    available_services: ["email", "calendar", "drive", "contacts", "sheets", "docs"],
    completion_mode: "manual_localhost_url",
  };
}

let root: Root | null = null;
let container: HTMLDivElement | null = null;

async function flush() {
  for (let i = 0; i < 5; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount(url = "/connections") {
  container = document.createElement("div");
  document.body.appendChild(container);
  const created = createRoot(container);
  root = created;
  await act(async () =>
    created.render(
      <MemoryRouter initialEntries={[url]}>
        <ConnectionsPage />
      </MemoryRouter>,
    ),
  );
  await flush();
}

function el<T extends Element>(selector: string): T {
  const found = document.querySelector<T>(selector);
  expect(found, selector).not.toBeNull();
  return found!;
}

function buttonByText(text: string, scope: ParentNode = document): HTMLButtonElement {
  const found = Array.from(scope.querySelectorAll("button")).find((node) => node.textContent?.trim() === text);
  expect(found, `кнопка «${text}»`).toBeTruthy();
  return found as HTMLButtonElement;
}

async function click(node: Element) {
  await act(async () => (node as HTMLElement).click());
  await flush();
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getConnections.mockResolvedValue(connections([MAIN, DESIGNER, LAWYER, SMM]));
  api.setGoogleWorkspaceSharing.mockResolvedValue({ source_profile: "default", profiles: [] });
  api.revokeGoogleWorkspace.mockResolvedValue({ status: "revoked", remote_revoked: true });
  api.checkGoogleWorkspaceService.mockResolvedValue({ service: "calendar", status: "ok", checked_at: "" });
  api.getGoogleWorkspaceStatus.mockResolvedValue(status());
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  container?.remove();
  root = null;
  container = null;
  document.body.innerHTML = "";
});

describe("Экран «Сервисы»", () => {
  it("показывает, через какого агента подключён Google и кому он доступен", async () => {
    await mount();
    const block = el<HTMLElement>("[data-google-source='default']");
    expect(block.textContent).toContain("Через агента «Главный агент»");
    expect(block.textContent).toContain("Календарь");
    expect(block.querySelector("[data-connection-agent='designer']")?.textContent).toContain(
      "Пользуется этим подключением",
    );
    // Готовому агенту без навыка — честно о том, что доступно сразу.
    expect(block.querySelector("[data-connection-agent='designer']")?.textContent).toContain(
      "Календарь доступен сразу; диск — после установки навыка Google Workspace.",
    );
    expect(block.querySelector("[data-connection-agent='smm']")?.textContent).toContain("Своё подключение Google");
    // У агента со своим подключением переключателя нет — сервер бы отказал.
    expect(block.querySelector("[data-connection-agent='smm'] [role='switch']")).toBeNull();
    expect(el<HTMLElement>("[data-google-source='smm']").textContent).toContain("Через агента «SMM»");
  });

  it("«Доступно всем агентам» открывает подключение всем, кому можно, одним запросом", async () => {
    await mount();
    const block = el<HTMLElement>("[data-google-source='default']");
    const all = block.querySelector<HTMLButtonElement>("[role='switch'][aria-labelledby='grant-default-all']")!;
    expect(all.getAttribute("aria-checked")).toBe("false");
    await click(all);
    expect(api.setGoogleWorkspaceSharing).toHaveBeenCalledWith(["designer", "lawyer"], "default");
    expect(api.getConnections).toHaveBeenCalledTimes(2);
  });

  it("переключатель агента меняет только его доступ", async () => {
    await mount();
    await click(el("[aria-label='Доступ к Google для агента «Юрист»']"));
    expect(api.setGoogleWorkspaceSharing).toHaveBeenLastCalledWith(["designer", "lawyer"], "default");
    await click(el("[aria-label='Доступ к Google для агента «Дизайнер»']"));
    expect(api.setGoogleWorkspaceSharing).toHaveBeenLastCalledWith([], "default");
  });

  it("отключение Google сначала закрывает общий доступ, потом отзывает", async () => {
    await mount();
    const block = el<HTMLElement>("[data-google-source='default']");
    await click(buttonByText("Отключить Google", block));
    expect(document.body.textContent).toContain("Доступ потеряют «Главный агент» и ещё 1: Дизайнер.");
    await click(buttonByText("Отключить"));
    expect(api.setGoogleWorkspaceSharing).toHaveBeenCalledWith([], "default");
    expect(api.revokeGoogleWorkspace).toHaveBeenCalledWith("default");
    const order = [
      api.setGoogleWorkspaceSharing.mock.invocationCallOrder[0],
      api.revokeGoogleWorkspace.mock.invocationCallOrder[0],
    ];
    expect(order[0]).toBeLessThan(order[1]);
  });

  it("проверяет календарь тем же маршрутом, что и раньше", async () => {
    await mount();
    await click(buttonByText("Проверить календарь", el("[data-google-source='default']")));
    expect(api.checkGoogleWorkspaceService).toHaveBeenCalledWith("calendar", "default");
  });

  it("из «Календаря» открывает подключение для названного агента", async () => {
    api.getConnections.mockResolvedValue(connections([row("default"), LAWYER]));
    await mount("/connections?connect=calendar&profile=default");
    expect(document.body.textContent).toContain("Подключить Google");
    expect(api.getGoogleWorkspaceStatus).toHaveBeenCalledWith("default");
    const calendar = Array.from(document.querySelectorAll<HTMLLabelElement>("label")).find((node) =>
      node.textContent?.includes("Google Calendar"),
    );
    expect(calendar?.querySelector("input")?.checked).toBe(true);
  });

  it("переподключение из «Календаря» показывает карточку источника", async () => {
    api.getGoogleWorkspaceStatus.mockResolvedValue(
      status({ state: "connected", services: ["drive"], shared_with: [] }),
    );
    api.getConnections.mockResolvedValue(
      connections([{ ...MAIN, services: ["drive"], shared_with: [] }, LAWYER]),
    );
    await mount("/connections?connect=calendar&profile=default");
    expect(document.body.textContent).toContain("Чтобы добавить календарь, отключите Google у агента «Главный агент»");
    expect(api.getGoogleWorkspaceStatus).toHaveBeenCalledWith("default");
  });

  it("без приложения на сервере честно отправляет в поддержку", async () => {
    api.getConnections.mockResolvedValue(connections([row("default")], false));
    await mount();
    expect(document.body.textContent).toContain("Подключение Google на этом сервере ещё не настроено");
    expect(document.body.textContent).not.toContain("Подключить Google");
  });

  it("если сводка не загрузилась — говорит об этом и даёт повторить", async () => {
    api.getConnections.mockRejectedValueOnce(new Error("offline"));
    await mount();
    expect(document.body.textContent).toContain("Список подключений не загрузился");
    await click(buttonByText("Повторить"));
    expect(document.querySelector("[data-google-source='default']")).not.toBeNull();
  });
});
