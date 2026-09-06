// @vitest-environment jsdom

import { act, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PageHeaderContext } from "@/contexts/page-header-context";
import { ROLE_STARTERS } from "@/lib/agent-wizard";

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
  // Виртуальный агрегатор: в мастере не показывается.
  { name: "Mixture of Agents", slug: "moa", models: ["default"], authenticated: true },
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

async function click(element: Element | undefined | null) {
  expect(element).toBeTruthy();
  await act(async () => {
    (element as HTMLElement).click();
  });
}

/** Открыть Select и выбрать пункт по подписи. */
async function pickOption(trigger: Element | null, text: string) {
  await click(trigger);
  const option = Array.from(
    document.body.querySelectorAll('[role="option"]'),
  ).find((node) => node.textContent?.includes(text));
  await click(option);
}

/** Пункты списка именно этого Select — соседний может ещё дозакрываться. */
function optionLabels(trigger: Element | null): string[] {
  const listbox = document.getElementById(trigger?.getAttribute("aria-controls") ?? "");
  return Array.from(listbox?.querySelectorAll('[role="option"]') ?? []).map(
    (node) => node.textContent ?? "",
  );
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

function failedReply(status: number, message: string) {
  return new Response(
    JSON.stringify({ error: { message, type: "server_error" } }),
    { status, headers: { "Content-Type": "application/json" } },
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
const providerSelect = () => container.querySelector<HTMLButtonElement>("#pb-provider");
const modelSelect = () => container.querySelector<HTMLButtonElement>("#pb-model");
const location = () => container.querySelector('[data-testid="location"]')?.textContent;

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
  vi.useRealTimers();
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

  it("заготовка роли подставляет имя и текст, а свой текст можно вернуть", async () => {
    await openWizard();
    const secretary = ROLE_STARTERS.find((starter) => starter.id === "secretary")!;
    const requests = ROLE_STARTERS.find((starter) => starter.id === "requests")!;

    await click(container.querySelector('[data-starter="secretary"]'));
    expect(nameInput().value).toBe("Секретарь");
    expect(roleInput().value).toBe(secretary.role);
    expect(
      container.querySelector('[data-starter="secretary"]')?.getAttribute("aria-pressed"),
    ).toBe("true");

    // Имя уже есть — вторая заготовка его не перебивает, а текст меняет
    // без предупреждения: он был заготовкой, а не словами владельца.
    await click(container.querySelector('[data-starter="requests"]'));
    expect(nameInput().value).toBe("Секретарь");
    expect(roleInput().value).toBe(requests.role);
    expect(container.textContent).not.toContain("Вернуть мой текст");

    await enterText(roleInput(), "Считаешь сметы по моим расценкам.");
    await click(container.querySelector('[data-starter="secretary"]'));
    expect(roleInput().value).toBe(secretary.role);
    expect(container.textContent).toContain("Заготовка заменила ваш текст.");
    await click(findButton("Вернуть мой текст"));
    expect(roleInput().value).toBe("Считаешь сметы по моим расценкам.");
    expect(container.textContent).not.toContain("Вернуть мой текст");
  });

  it("подставляет модель главного агента и показывает, что доступ настроен", async () => {
    await openWizard();

    expect(providerSelect()?.textContent).toContain("Как у главного агента");
    expect(modelSelect()).toBeNull();
    expect(container.textContent).toContain(
      "dario · claude-opus-5[1m] — доступ настроен, агент ответит сразу.",
    );
  });

  it("выбирает модель в два шага: провайдер по-русски, потом его модели", async () => {
    await openWizard();

    await click(providerSelect());
    expect(optionLabels(providerSelect())).toEqual([
      "Как у главного агента",
      "dario",
      "Anthropic (Claude) — нет ключа",
    ]);
    await click(
      Array.from(document.body.querySelectorAll('[role="option"]')).find((node) =>
        node.textContent?.trim() === "dario",
      ),
    );

    // Модель провайдера по умолчанию — та же, что у главного агента.
    expect(modelSelect()?.textContent).toContain("claude-opus-5[1m]");
    await click(modelSelect());
    expect(optionLabels(modelSelect())).toEqual(["claude-opus-5[1m]", "claude-sonnet-5"]);
    const modelListbox = document.getElementById(
      modelSelect()?.getAttribute("aria-controls") ?? "",
    );
    await click(
      Array.from(modelListbox?.querySelectorAll('[role="option"]') ?? []).find((node) =>
        node.textContent?.includes("claude-sonnet-5"),
      ),
    );
    expect(container.textContent).toContain("Доступ к «dario» настроен");

    await enterText(nameInput(), "Учитель китайского");
    await click(findButton("Создать агента"));
    await flush();
    expect(apiMocks.createProfile.mock.calls[0][0]).toMatchObject({
      provider: "custom:dario",
      model: "claude-sonnet-5",
    });
  });

  it("предупреждает о провайдере без ключа", async () => {
    await openWizard();

    await pickOption(providerSelect(), "Anthropic (Claude)");
    expect(modelSelect()?.textContent).toContain("claude-opus-5[1m]");
    expect(container.textContent).toContain(
      "Агент не ответит, пока в «Ключах» не появится доступ к «Anthropic (Claude)».",
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
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(container.textContent).toContain("Проверка агента");
    // Сохранённое видно отдельно от ответа.
    expect(container.textContent).toContain("Сохранено");
    expect(container.textContent).toContain("своими словами, плюс правила общения по-русски");
    expect(container.textContent).toContain("dario · claude-opus-5[1m]");
    expect(container.textContent).toContain(
      "Агент отвечает: «Я — Учитель китайского.»",
    );

    await click(findButton("Открыть чат"));
    expect(location()).toBe("/agents?agent=uchitel-kitayskogo");
  });

  it("отказ доступа (401 подписки) объясняет как доступ, а не как сломанного агента", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        failedReply(502, "HTTP 401: OAuth access token has expired. Re-authenticate to continue."),
      ),
    );
    await openWizard();
    await enterText(nameInput(), "Учитель китайского");
    await click(findButton("Создать агента"));
    await flush();

    expect(container.textContent).toContain("не отвечает");
    expect(container.textContent).toContain("Нет доступа к модели");
    expect(container.textContent).toContain("Сам агент сохранён: имя, роль и модель на месте.");
    expect(container.textContent).toContain("Ответ сервера: HTTP 401: OAuth access token has expired.");
    // Роль не писали — шаг проверки говорит об этом прямо.
    expect(container.textContent).toContain("не задана — только имя и правила общения");

    await click(findButton("Повторить проверку"));
    await flush();
    expect(globalThis.fetch).toHaveBeenCalledTimes(2);

    await click(findButton("Открыть «Ключи»"));
    expect(location()).toBe("/env?profile=uchitel-kitayskogo");
  });

  it("перегрузка провайдера (503) не отправляет в «Ключи»", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        failedReply(503, "HTTP 503: all accounts are rate-limited or in auth cool-down"),
      ),
    );
    await openWizard();
    await enterText(nameInput(), "Учитель китайского");
    await click(findButton("Создать агента"));
    await flush();

    expect(container.textContent).toContain("Сам агент сохранён");
    expect(container.textContent).not.toContain("проверьте его в «Ключах»");
    expect(findButton("Повторить проверку")).toBeTruthy();
    expect(findButton("Открыть чат")).toBeTruthy();
  });

  it("проверку можно не ждать, а по истечении срока она завершается сама", async () => {
    vi.useFakeTimers();
    const pending = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("fetch", pending);
    await openWizard();
    await enterText(nameInput(), "Учитель китайского");
    await click(findButton("Создать агента"));
    await flush();

    expect(container.textContent).toContain("Спрашиваю агента, кто он…");
    expect(findButton("Не ждать — открыть чат")).toBeTruthy();

    await act(async () => {
      vi.advanceTimersByTime(90_001);
      await Promise.resolve();
    });
    await flush();
    expect(container.textContent).toContain("Модель не ответила вовремя");
    expect(findButton("Повторить проверку")).toBeTruthy();
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
    await pickOption(container.querySelector("#pb-clone"), "Секретарь (sekretar)");
    await flush();

    expect(providerSelect()?.textContent).toContain("Как у агента-источника");
    expect(container.textContent).toContain("dario · claude-sonnet-5 — доступ настроен");

    await enterText(nameInput(), "Второй секретарь");
    await click(findButton("Создать агента"));
    await flush();
    expect(apiMocks.createProfile.mock.calls[0][0]).toMatchObject({
      clone_from: "sekretar",
      model: "claude-sonnet-5",
    });
  });
});
