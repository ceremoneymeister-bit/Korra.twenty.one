// @vitest-environment jsdom

import { act, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PageHeaderContext } from "@/contexts/page-header-context";

const apiMocks = vi.hoisted(() => ({
  getProfiles: vi.fn(),
  getModelOptions: vi.fn(),
  createProfile: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, ...apiMocks } };
});

// Орбита рисует себя через requestAnimationFrame и canvas — в jsdom это шум.
vi.mock("thinking-orbs", () => ({
  ThinkingOrb: ({ state }: { state: string }) => (
    <span data-orb={state} data-testid="orb" />
  ),
}));

// Каталог профилей разделов панели — мастер обязан его обновить после
// создания, иначе «Ключи» нового агента откроются для главного.
const scopeMocks = vi.hoisted(() => ({ refreshProfiles: vi.fn() }));

vi.mock("@/contexts/useProfileScope", () => ({
  useProfileScope: () => ({
    profile: "",
    currentProfile: "default",
    profiles: [],
    setProfile: () => {},
    refreshProfiles: scopeMocks.refreshProfiles,
  }),
}));

import ProfileBuilderPage, { PROBE_PROMPT } from "./ProfileBuilderPage";

let container: HTMLDivElement;
let root: Root;

const PROFILES = [
  {
    name: "default",
    path: "/opt/data",
    is_default: true,
    model: "claude-opus-5[1m]",
    provider: "custom:dario",
    has_env: true,
    skill_count: 59,
    gateway_running: true,
    description: "Главный агент",
    description_auto: false,
    display_name: "Корра",
    distribution_name: null,
    distribution_version: null,
    distribution_source: null,
    has_alias: false,
  },
  {
    name: "sekretar",
    path: "/opt/data/profiles/sekretar",
    is_default: false,
    model: "claude-sonnet-5",
    provider: "custom:dario",
    has_env: true,
    skill_count: 59,
    gateway_running: false,
    description: "Секретарь",
    description_auto: false,
    display_name: "Секретарь",
    distribution_name: null,
    distribution_version: null,
    distribution_source: null,
    has_alias: false,
  },
];

const PROVIDERS = [
  {
    name: "Anthropic",
    slug: "anthropic",
    models: ["claude-opus-5[1m]"],
    authenticated: false,
  },
  {
    name: "dario",
    slug: "custom:dario",
    models: ["claude-opus-5[1m]", "claude-sonnet-5"],
    authenticated: true,
  },
];

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

/** Заголовок раздела мастер ставит через тот же контекст, что и приложение. */
function PageHeaderHost({ children }: { children: ReactNode }) {
  const [title, setTitle] = useState<string | null>(null);
  return (
    <PageHeaderContext.Provider
      value={{ setAfterTitle: () => {}, setEnd: () => {}, setTitle }}
    >
      <div data-testid="page-title">{title}</div>
      {children}
    </PageHeaderContext.Provider>
  );
}

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <output data-testid="location">{`${pathname}${search}`}</output>;
}

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function findButton(text: string, scope: ParentNode = container) {
  return Array.from(scope.querySelectorAll("button")).find((button) =>
    button.textContent?.includes(text),
  );
}

async function click(element: Element | undefined) {
  expect(element).toBeTruthy();
  await act(async () => {
    (element as HTMLElement).click();
  });
}

async function enterText(
  input: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  const proto =
    input instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function okReply(content: string) {
  return new Response(
    JSON.stringify({
      choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

/** Открыть мастер и дождаться списка профилей и моделей. */
async function openWizard() {
  await render(
    <MemoryRouter initialEntries={["/profiles/new"]}>
      <PageHeaderHost>
        <ProfileBuilderPage />
      </PageHeaderHost>
      <LocationProbe />
    </MemoryRouter>,
  );
  await flush();
}

const nameInput = () => container.querySelector<HTMLInputElement>("#pb-name")!;
const idInput = () => container.querySelector<HTMLInputElement>("#pb-id")!;
const roleInput = () => container.querySelector<HTMLTextAreaElement>("#pb-role")!;

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      addEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
      matches: false,
      media: query,
      onchange: null,
      removeEventListener: vi.fn(),
    })),
  );
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  apiMocks.getProfiles.mockResolvedValue({ profiles: PROFILES });
  apiMocks.getModelOptions.mockResolvedValue({
    providers: PROVIDERS,
    provider: "custom:dario",
    model: "claude-opus-5[1m]",
  });
  apiMocks.createProfile.mockResolvedValue({
    ok: true,
    name: "uchitel-kitayskogo",
    path: "/opt/data/profiles/uchitel-kitayskogo",
    model_set: true,
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okReply("Я — Учитель китайского.")));
  scopeMocks.refreshProfiles.mockReset();
  scopeMocks.refreshProfiles.mockResolvedValue(undefined);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("ProfileBuilderPage — мастер создания агента", () => {
  it("называется «Новый агент» и спрашивает имя и роль, а не slug", async () => {
    await openWizard();

    expect(document.querySelector('[data-testid="page-title"]')?.textContent).toBe(
      "Новый агент",
    );
    expect(container.textContent).toContain("Как зовут агента");
    expect(container.textContent).toContain("Что он делает и как себя ведёт");
    expect(container.textContent).not.toContain("MCP");
    // Пока имя не введено, создавать нечего.
    expect(findButton("Создать агента")?.disabled).toBe(true);
  });

  it("выводит системное имя транслитом и обходит занятые", async () => {
    await openWizard();

    await enterText(nameInput(), "Учитель китайского");
    expect(idInput().value).toBe("uchitel-kitayskogo");

    await enterText(nameInput(), "Секретарь");
    expect(idInput().value).toBe("sekretar-2");
    expect(findButton("Создать агента")?.disabled).toBe(false);
  });

  it("даёт поправить системное имя руками и объясняет ошибку", async () => {
    await openWizard();
    await enterText(nameInput(), "Секретарь");

    await enterText(idInput(), "Секретарь");
    expect(container.textContent).toContain("Только строчные латинские буквы");
    expect(findButton("Создать агента")?.disabled).toBe(true);

    await enterText(idInput(), "sekretar");
    expect(container.textContent).toContain("уже есть");

    await enterText(idInput(), "assistant");
    expect(findButton("Создать агента")?.disabled).toBe(false);

    // Пустое поле возвращает транслит.
    await enterText(idInput(), "");
    expect(idInput().value).toBe("sekretar-2");
  });

  it("подставляет модель главного агента и показывает, что ключ настроен", async () => {
    await openWizard();

    const trigger = container.querySelector<HTMLButtonElement>("#pb-model");
    expect(trigger?.textContent).toContain("dario · claude-opus-5[1m] — готов");
    expect(container.textContent).toContain("Ключ провайдера dario настроен.");
  });

  it("предупреждает о провайдере без ключа", async () => {
    await openWizard();

    await click(container.querySelector("#pb-model")!);
    const option = Array.from(
      document.body.querySelectorAll('[role="option"]'),
    ).find((node) => node.textContent?.includes("Anthropic"));
    await click(option);

    expect(container.textContent).toContain(
      "Агент не ответит, пока ключ провайдера Anthropic не появится в «Ключах».",
    );
  });

  it("создаёт агента одним запросом с ролью и именем, проверяет и ведёт в чат", async () => {
    await openWizard();
    await enterText(nameInput(), "Учитель китайского");
    await enterText(
      roleInput(),
      "Учишь меня китайскому по 15 минут в день. Проверяешь домашние задания.",
    );
    await click(findButton("Создать агента"));
    await flush();

    expect(apiMocks.createProfile).toHaveBeenCalledTimes(1);
    const body = apiMocks.createProfile.mock.calls[0][0];
    expect(body).toMatchObject({
      name: "uchitel-kitayskogo",
      clone_from: null,
      clone_all: false,
      no_skills: false,
      display_name: "Учитель китайского",
      description: "Учишь меня китайскому по 15 минут в день.",
      provider: "custom:dario",
      model: "claude-opus-5[1m]",
    });
    expect(body.soul).toContain("Ты — Учитель китайского, агент в системе Korra.");
    expect(body.soul).toContain("Проверяешь домашние задания.");
    expect(body.soul).toContain("## Как общаться");
    // Каталог профилей разделов обновлён — «Ключи»/«Навыки» нового агента
    // откроются для него, а не для главного.
    expect(scopeMocks.refreshProfiles).toHaveBeenCalledTimes(1);

    // Проверка идёт тем же маршрутом, что и чат, и просит представиться.
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/chat/completions?profile=uchitel-kitayskogo");
    expect(JSON.parse(String(init.body))).toMatchObject({
      messages: [{ role: "user", content: PROBE_PROMPT }],
      stream: false,
    });
    expect(container.textContent).toContain("Проверка агента");
    expect(container.textContent).toContain(
      "Агент отвечает: «Я — Учитель китайского.»",
    );

    await click(findButton("Открыть чат"));
    expect(
      container.querySelector('[data-testid="location"]')?.textContent,
    ).toBe("/agents?agent=uchitel-kitayskogo");
  });

  it("при отказе провайдера объясняет, куда идти, и даёт повторить", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: { message: "HTTP 401: invalid x-api-key", type: "server_error" },
          }),
          { status: 502, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    await openWizard();
    await enterText(nameInput(), "Учитель китайского");
    await click(findButton("Создать агента"));
    await flush();

    expect(container.textContent).toContain("не отвечает");
    expect(container.textContent).toContain("HTTP 401: invalid x-api-key");
    expect(container.textContent).toContain("проверьте его в «Ключах»");

    await click(findButton("Повторить проверку"));
    await flush();
    expect(globalThis.fetch).toHaveBeenCalledTimes(2);

    await click(findButton("Открыть «Ключи»"));
    expect(
      container.querySelector('[data-testid="location"]')?.textContent,
    ).toBe("/env?profile=uchitel-kitayskogo");
  });

  it("сбой обновления каталога не мешает проверке и не повторяет создание", async () => {
    scopeMocks.refreshProfiles.mockRejectedValueOnce(new Error("503: занято"));
    await openWizard();
    await enterText(nameInput(), "Учитель китайского");
    await click(findButton("Создать агента"));
    await flush();

    expect(apiMocks.createProfile).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("Агент отвечает: «Я — Учитель китайского.»");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("ошибку создания показывает на форме и не уходит в проверку", async () => {
    apiMocks.createProfile.mockRejectedValueOnce(
      new Error('400: {"detail":"Профиль с таким именем уже есть"}'),
    );
    await openWizard();
    await enterText(nameInput(), "Учитель китайского");
    await click(findButton("Создать агента"));
    await flush();

    expect(container.querySelector('[role="alert"]')?.textContent).toBe(
      "Профиль с таким именем уже есть",
    );
    expect(container.textContent).not.toContain("Проверка агента");
    expect(globalThis.fetch).not.toHaveBeenCalled();
    expect(scopeMocks.refreshProfiles).not.toHaveBeenCalled();
    expect(nameInput().value).toBe("Учитель китайского");
  });

  it("копирование настроек спрятано под «Дополнительно» и меняет модель по умолчанию", async () => {
    await openWizard();
    expect(container.querySelector("#pb-clone")).toBeNull();

    await click(findButton("Дополнительно"));
    await click(container.querySelector("#pb-clone")!);
    const option = Array.from(
      document.body.querySelectorAll('[role="option"]'),
    ).find((node) => node.textContent?.includes("Секретарь (sekretar)"));
    await click(option);
    await flush();

    expect(
      container.querySelector<HTMLButtonElement>("#pb-model")?.textContent,
    ).toContain("claude-sonnet-5");

    await enterText(nameInput(), "Второй секретарь");
    await click(findButton("Создать агента"));
    await flush();
    expect(apiMocks.createProfile.mock.calls[0][0]).toMatchObject({
      clone_from: "sekretar",
      model: "claude-sonnet-5",
    });
  });
});
