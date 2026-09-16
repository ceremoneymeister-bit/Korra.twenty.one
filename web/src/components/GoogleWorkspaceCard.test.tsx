// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { GoogleWorkspaceCard } from "./GoogleWorkspaceCard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const scope = vi.hoisted(() => ({ profile: "writer", currentProfile: "default", profiles: [] as { name: string; display_name: string }[] }));
const apiMocks = vi.hoisted(() => ({
  getGoogleWorkspaceStatus: vi.fn(),
  revokeGoogleWorkspace: vi.fn(),
}));

vi.mock("@/contexts/useProfileScope", () => ({
  useProfileScope: () => scope,
}));

vi.mock("@/lib/api", () => ({
  api: {
    getGoogleWorkspaceStatus: apiMocks.getGoogleWorkspaceStatus,
    revokeGoogleWorkspace: apiMocks.revokeGoogleWorkspace,
  },
}));

let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
  scope.profile = "writer";
  scope.profiles = [];
  apiMocks.getGoogleWorkspaceStatus.mockReset().mockResolvedValue({
    app: { configured: true, credential_type: "installed" },
    connection: { state: "not_connected", services: [] },
    pending: { active: true, services: ["drive"], expires_at: 1 },
    available_services: ["drive"],
    completion_mode: "manual_localhost_url",
  });
  apiMocks.revokeGoogleWorkspace.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});

async function renderCard() {
  await act(async () => {
    root.render(<GoogleWorkspaceCard onError={vi.fn()} onSuccess={vi.fn()} />);
    await Promise.resolve();
  });
}

it("clears the pasted localhost flow when the selected profile changes", async () => {
  await renderCard();
  const input = host.querySelector<HTMLInputElement>(
    'input[aria-label="Полный адрес возврата Google"]',
  );
  expect(input).not.toBeNull();

  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(
      input,
      "http://localhost/?state=writer&code=writer",
    );
    input!.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(input!.value).toContain("state=writer");

  scope.profile = "research";
  await renderCard();

  expect(
    host.querySelector<HTMLInputElement>('input[aria-label="Полный адрес возврата Google"]')?.value,
  ).toBe("");
  expect(apiMocks.getGoogleWorkspaceStatus).toHaveBeenCalledTimes(2);
});

it("warns when only the local grant was removed", async () => {
  apiMocks.getGoogleWorkspaceStatus.mockResolvedValue({
    app: { configured: true, credential_type: "installed" },
    connection: { state: "connected", services: ["drive"] },
    pending: { active: false },
    available_services: ["drive"],
    completion_mode: "manual_localhost_url",
  });
  apiMocks.revokeGoogleWorkspace.mockResolvedValue({
    status: "revoked",
    remote_revoked: false,
  });
  const onError = vi.fn();
  const onSuccess = vi.fn();

  await act(async () => {
    root.render(<GoogleWorkspaceCard onError={onError} onSuccess={onSuccess} />);
    await Promise.resolve();
  });
  const button = Array.from(host.querySelectorAll("button")).find(element =>
    element.textContent?.includes("Отключить Google"),
  );
  expect(button).toBeDefined();
  await act(async () => {
    button!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(apiMocks.revokeGoogleWorkspace).toHaveBeenCalledTimes(1);
  expect(onSuccess).not.toHaveBeenCalled();
  expect(onError).toHaveBeenCalledWith(expect.stringContaining("Google не подтвердил отзыв"));
  expect(onError).toHaveBeenCalledWith(expect.stringContaining("настройках Google Аккаунта"));
});

it("explains and detaches shared access without reporting a failed remote revoke", async () => {
  apiMocks.getGoogleWorkspaceStatus.mockResolvedValue({
    app: { configured: true, credential_type: "installed" },
    connection: {
      state: "connected",
      services: ["drive"],
      shared_from: "assistant",
    },
    pending: { active: false },
    available_services: ["drive"],
    completion_mode: "manual_localhost_url",
  });
  apiMocks.revokeGoogleWorkspace.mockResolvedValue({
    status: "detached",
    remote_revoked: false,
  });
  const onError = vi.fn();
  const onSuccess = vi.fn();

  await act(async () => {
    root.render(<GoogleWorkspaceCard onError={onError} onSuccess={onSuccess} />);
    await Promise.resolve();
  });
  expect(host.textContent).toContain("Источник подключения:");
  expect(host.textContent).toContain("assistant");
  const button = Array.from(host.querySelectorAll("button")).find(element =>
    element.textContent?.includes("Отключить общий доступ"),
  );
  await act(async () => {
    button!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(onError).not.toHaveBeenCalled();
  expect(onSuccess).toHaveBeenCalledWith(
    "Общий доступ к Google отключён для выбранного агента.",
  );
});

it("shows only the proven services for a compatible legacy grant", async () => {
  apiMocks.getGoogleWorkspaceStatus.mockResolvedValue({
    app: { configured: true, credential_type: "installed" },
    connection: {
      state: "reauthorization_required",
      reason: "legacy_untracked",
      legacy_scope_count: 2,
      usable_services: ["drive", "docs"],
      legacy_compatible: true,
    },
    pending: { active: false },
    available_services: ["email", "calendar", "drive", "contacts", "sheets", "docs"],
    completion_mode: "manual_localhost_url",
  });

  await renderCard();

  expect(host.textContent).toContain("Работает с текущими правами");
  expect(host.textContent).toContain("Доступны сейчас");
  expect(host.textContent).not.toContain("Нужно переподключить");
  expect(host.textContent).not.toContain("Gmail");
  expect(host.textContent).toContain("Google Drive");
  expect(host.textContent).toContain("Google Docs");
  expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  expect(host.textContent).not.toContain("Какие сервисы разрешить этому агенту");
});

it("ignores the previous agent's slow status after switching", async () => {
  let finishOld!: (value: unknown) => void;
  const state = (service: string) => ({
    app: { configured: true }, connection: { state: "connected", services: [service] },
    pending: { active: false }, available_services: [service],
  });
  apiMocks.getGoogleWorkspaceStatus.mockReset()
    .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
    .mockResolvedValueOnce(state("docs"));
  await renderCard();
  scope.profile = "research";
  await renderCard();
  expect(host.textContent).toContain("Google Docs");
  await act(async () => { finishOld(state("drive")); });
  expect(host.textContent).toContain("Google Docs");
  expect(host.textContent).not.toContain("Google Drive");
  expect(apiMocks.getGoogleWorkspaceStatus).toHaveBeenLastCalledWith("research");
});


it("names shared consumers and prevents revoking their source", async () => {
  scope.profiles = [{ name: "smm", display_name: "Редактор" }];
  apiMocks.getGoogleWorkspaceStatus.mockResolvedValue({
    app: { configured: true }, connection: { state: "connected", services: ["drive"], shared_with: ["smm"] },
    pending: { active: false }, available_services: ["drive"],
  });
  await renderCard();
  expect(host.textContent).toContain("Редактор");
  expect(host.textContent).toContain("сначала отключите общий доступ");
  const revoke = Array.from(host.querySelectorAll("button")).find(button => button.textContent?.includes("Отключить Google"))!;
  expect(revoke.disabled).toBe(true);
  expect(apiMocks.revokeGoogleWorkspace).not.toHaveBeenCalled();
});

it("keeps an in-flight detach bound to its original agent", async () => {
  let finishDetach!: (value: unknown) => void;
  apiMocks.getGoogleWorkspaceStatus.mockResolvedValue({
    app: { configured: true }, connection: { state: "connected", services: ["drive"], shared_from: "assistant" },
    pending: { active: false }, available_services: ["drive"],
  });
  apiMocks.revokeGoogleWorkspace.mockImplementation(() => new Promise(resolve => { finishDetach = resolve; }));
  const onSuccess = vi.fn();
  const onError = vi.fn();
  await act(async () => { root.render(<GoogleWorkspaceCard onSuccess={onSuccess} onError={onError} />); });
  const button = Array.from(host.querySelectorAll("button")).find(node => node.textContent?.includes("Отключить общий доступ"))!;
  await act(async () => { button.click(); });
  expect(apiMocks.revokeGoogleWorkspace).toHaveBeenCalledWith("writer");
  scope.profile = "research";
  await act(async () => { root.render(<GoogleWorkspaceCard onSuccess={onSuccess} onError={onError} />); });
  await act(async () => { finishDetach({ status: "detached", remote_revoked: false }); });
  expect(onSuccess).not.toHaveBeenCalled();
  expect(onError).not.toHaveBeenCalled();
  expect(host.textContent).toContain("Источник подключения:");
});
