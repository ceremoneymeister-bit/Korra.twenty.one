// @vitest-environment jsdom

import { act, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PageHeaderContext } from "@/contexts/page-header-context";

const apiMocks = vi.hoisted(() => ({
  getProfiles: vi.fn(),
  getActiveProfile: vi.fn(),
  getModelOptions: vi.fn(),
  createProfile: vi.fn(),
  updateProfileDisplayName: vi.fn(),
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

import ProfilesPage, { buildModelChoices } from "./ProfilesPage";

let container: HTMLDivElement;
let root: Root;

const PROFILES = [
  {
    name: "default",
    path: "/root/.korra",
    is_default: true,
    model: "claude-opus-4-5",
    provider: "custom:dario",
    has_env: true,
    skill_count: 3,
    gateway_running: true,
    description: "Главный агент",
    description_auto: false,
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
    models: ["claude-opus-4-5"],
    authenticated: false,
  },
  {
    name: "Nous",
    slug: "nous",
    models: ["hermes-4-70b"],
    authenticated: true,
  },
  {
    name: "Dario",
    slug: "custom:dario",
    models: ["claude-opus-4-5", "claude-sonnet-4-5"],
    authenticated: true,
  },
];

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

/** Кнопка «Создать» живёт в шапке страницы — отдаём её через тот же контекст,
 *  что и приложение, и рисуем рядом. */
function PageHeaderHost({ children }: { children: ReactNode }) {
  const [end, setEnd] = useState<ReactNode>(null);
  return (
    <PageHeaderContext.Provider
      value={{ setAfterTitle: () => {}, setEnd, setTitle: () => {} }}
    >
      <div data-testid="page-header-end">{end}</div>
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

async function enterText(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

/** Открыть мастер и дождаться списка моделей. */
async function openWizard() {
  await render(
    <MemoryRouter initialEntries={["/profiles"]}>
      <PageHeaderHost>
        <ProfilesPage />
      </PageHeaderHost>
      <LocationProbe />
    </MemoryRouter>,
  );
  await flush();
  await click(
    findButton("Создать", document.querySelector('[data-testid="page-header-end"]')!),
  );
  await flush();
}

/** Заполнить имя и нажать «Создать» внутри мастера. */
async function submitWizard(name: string) {
  await enterText(
    container.querySelector<HTMLInputElement>("#profile-name")!,
    name,
  );
  const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
  await click(findButton("Создать", dialog));
  await flush();
}

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
  apiMocks.getActiveProfile.mockResolvedValue({
    active: "default",
    current: "default",
  });
  apiMocks.getModelOptions.mockResolvedValue({
    providers: PROVIDERS,
    provider: "custom:dario",
    model: "claude-opus-4-5",
  });
  apiMocks.createProfile.mockResolvedValue({
    ok: true,
    name: "buhgalter",
    path: "/root/.korra-buhgalter",
    model_set: true,
  });
  apiMocks.updateProfileDisplayName.mockResolvedValue({
    ok: true,
    display_name: "Бухгалтер",
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("buildModelChoices", () => {
  it("ставит провайдеров с ключами первыми и метит остальных", () => {
    const choices = buildModelChoices(PROVIDERS);

    expect(choices.map((c) => c.label)).toEqual([
      "Dario · claude-opus-4-5 — готов",
      "Dario · claude-sonnet-4-5 — готов",
      "Anthropic · claude-opus-4-5 — нет ключа",
    ]);
    expect(choices.every((c) => c.provider !== "nous")).toBe(true);
    expect(choices[0].ready).toBe(true);
    expect(choices[2].ready).toBe(false);
  });

  it("считает провайдера готовым, пока движок не сказал обратного", () => {
    const choices = buildModelChoices([
      { name: "Свой", slug: "custom:local", models: ["qwen"] },
    ]);

    expect(choices).toHaveLength(1);
    expect(choices[0].ready).toBe(true);
  });
});

describe("ProfilesPage — мастер создания агента", () => {
  it("подставляет модель профиля-источника, а не первую в списке", async () => {
    await openWizard();

    const trigger = container.querySelector<HTMLButtonElement>("#profile-model");
    expect(trigger?.textContent).toContain("Dario · claude-opus-4-5 — готов");
    expect(container.textContent).toContain(
      "Ключ провайдера Dario настроен.",
    );
  });

  it("даёт выбрать провайдера без ключа, но предупреждает об этом", async () => {
    await openWizard();

    await click(container.querySelector("#profile-model")!);
    const option = Array.from(
      document.body.querySelectorAll('[role="option"]'),
    ).find((node) => node.textContent?.includes("Anthropic"));
    await click(option);

    expect(container.textContent).toContain(
      "Агент не ответит, пока ключ провайдера Anthropic не появится в «Ключах».",
    );
  });

  it("после создания показывает ответ агента и ведёт в его чат", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          choices: [
            {
              index: 0,
              message: { role: "assistant", content: "Готов" },
              finish_reason: "stop",
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await openWizard();
    await submitWizard("buhgalter");

    // Профиль уходит на сервер с моделью источника, а не с пустым выбором.
    expect(apiMocks.createProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "buhgalter",
        provider: "custom:dario",
        model: "claude-opus-4-5",
      }),
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/chat/completions?profile=buhgalter");
    expect(JSON.parse(String(init.body))).toMatchObject({
      messages: [{ role: "user", content: "Ответь одним словом: готов?" }],
      stream: false,
    });
    expect(
      new Headers(init.headers).get("X-Korra-Client-Message-Id"),
    ).toBeTruthy();

    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(dialog.textContent).toContain("Агент готов: «Готов»");

    await click(findButton("Открыть чат", dialog));
    expect(
      container.querySelector('[data-testid="location"]')?.textContent,
    ).toBe("/agents?agent=buhgalter");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it("показывает отказ провайдера и даёт повторить проверку", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            message: "HTTP 401: invalid x-api-key",
            type: "server_error",
            code: "agent_incomplete",
          },
        }),
        { status: 502, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await openWizard();
    await submitWizard("buhgalter");

    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(dialog.textContent).toContain(
      "Сервис временно недоступен. Повторите через минуту.",
    );
    expect(dialog.textContent).toContain("HTTP 401: invalid x-api-key");
    expect(dialog.textContent).toContain("Проверьте ключ провайдера в «Ключах»");
    expect(findButton("Открыть чат", dialog)).toBeUndefined();

    await click(findButton("Повторить проверку", dialog));
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("сохраняет имя для вкладки вместе с профилем", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            choices: [{ message: { content: "Готов" }, finish_reason: "stop" }],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await openWizard();
    await enterText(
      container.querySelector<HTMLInputElement>("#profile-display-name")!,
      "Бухгалтер",
    );
    await submitWizard("buhgalter");

    expect(apiMocks.updateProfileDisplayName).toHaveBeenCalledWith(
      "buhgalter",
      "Бухгалтер",
    );
  });
});
