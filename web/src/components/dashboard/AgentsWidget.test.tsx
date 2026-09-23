// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dashboardStateFixture, FIXTURE_NOW } from "@/components/dashboard/dashboard-state.fixture";
import { AGENTS_WIDGET } from "@/components/dashboard/widgets/AgentsWidget";
import type { WidgetSize } from "@/lib/dashboard-layout";
import { $dashboardState } from "@/lib/dashboard-state";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ getProfiles: vi.fn() }));
vi.mock(import("@/lib/api"), async (importOriginal) => ({
  ...(await importOriginal()),
  api: api as unknown as typeof import("@/lib/api").api,
}));

import {
  $chatRuns,
  $chatRunsReachable,
  $chatRunsUpdatedAt,
  refreshChatRuns,
  type ChatRun,
} from "@/lib/chat-runs";

/** Настоящий путь `lib/chat-runs`: ответ отдаётся транспортом, а не подменой
 *  экспортов — иначе тест проверял бы мок, а не работу карточки. */
function serveRuns(list: ChatRun[] | Error) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      if (String(url).includes("/api/chat/runs")) {
        if (list instanceof Error) throw list;
        return new Response(JSON.stringify({ runs: list }), {
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error("unexpected request");
    }),
  );
}

const PROFILES = [
  { name: "default", is_default: true, display_name: "Корра" },
  { name: "designer", is_default: false, display_name: "Дизайнер" },
];

let root: Root;
let container: HTMLDivElement;

async function mount(size: WidgetSize = "m", refreshRuns = true) {
  const { Body } = AGENTS_WIDGET;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Body size={size} />
      </MemoryRouter>,
    ),
  );
  if (refreshRuns) {
    // Хранилище работ — модульное и в этом файле переживает размонтирование
    // (nanostores снимает подписку отложенно), поэтому опрос запускаем явно.
    // Путь при этом настоящий: fetch → разбор → атомы.
    await act(async () => {
      await refreshChatRuns();
    });
  }
  await flush();
}

async function flush() {
  for (let i = 0; i < 4; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function click(element: Element) {
  await act(async () =>
    element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })),
  );
}

function button(text: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button")).find(
    (node) => node.textContent?.trim() === text,
  );
  expect(found, `кнопка «${text}»`).toBeTruthy();
  return found as HTMLButtonElement;
}

function links(): string[] {
  return Array.from(container.querySelectorAll("a")).map(
    (node) => node.getAttribute("href") ?? "",
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  api.getProfiles.mockResolvedValue({ profiles: PROFILES });
  $chatRuns.set([]);
  $chatRunsReachable.set(null);
  $chatRunsUpdatedAt.set(null);
  serveRuns([]);
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("Карточка «Агенты» на реальном источнике", () => {
  it("пока состав не пришёл, говорит об этом, а не показывает ноль занятых", async () => {
    let resolveProfiles: (value: unknown) => void = () => {};
    api.getProfiles.mockReturnValue(
      new Promise((resolve) => {
        resolveProfiles = resolve;
      }),
    );
    await mount();
    expect(container.textContent).toContain("Читаем ваших агентов");
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
    expect(container.textContent).not.toContain("никто не занят");

    await act(async () => resolveProfiles({ profiles: PROFILES }));
    expect(container.textContent).toContain("Корра");
  });

  it("показывает реальных агентов и ведёт к нужному агенту и чату", async () => {
    serveRuns([
      {
        message_id: "m1",
        session_id: "s-42",
        profile: "designer",
        status: "running",
        updated_at: 10,
        history_count: 1,
        user_message: { role: "user", content: "Собери презентацию" },
      },
    ]);
    await mount("l");

    expect(container.textContent).toContain("Дизайнер");
    expect(container.textContent).toContain("Работает");
    expect(container.textContent).toContain("Собери презентацию");
    expect(links()).toContain("/agents?agent=designer&resume=s-42");
    expect(links()).toContain("/agents?agent=default");
    expect(container.querySelector("[data-agents-live]")).not.toBeNull();
  });

  it("на компактном размере даёт число занятых, а переход живёт в шапке карточки", async () => {
    serveRuns([
      {
        message_id: "m1",
        session_id: "s-1",
        profile: "designer",
        status: "running",
        updated_at: 10,
        history_count: 1,
        user_message: { role: "user", content: "Работа" },
      },
    ]);
    await mount("s");

    expect(container.textContent).toContain("сейчас в работе");
    expect(container.textContent).toContain("из 2");
    // Переход карточки держит шапка: в содержимом плитки 1×1 он соревновался
    // бы за высоту с самим числом и обрезался первым.
    expect(links()).toEqual([]);
    expect(AGENTS_WIDGET.action).toEqual({
      label: "Все агенты",
      short: "Все",
      to: "/agents",
    });
  });

  it("сбой состава — честная ошибка с работающим повтором", async () => {
    api.getProfiles.mockRejectedValueOnce(new Error("offline"));
    await mount();

    expect(container.textContent).toContain("Не удалось прочитать агентов");
    expect(container.textContent).not.toContain("Корра");

    api.getProfiles.mockResolvedValue({ profiles: PROFILES });
    await click(button("Повторить"));
    expect(container.textContent).toContain("Корра");
    expect(container.textContent).not.toContain("Не удалось прочитать агентов");
  });

  it("недоступная активность помечается как последнее известное состояние", async () => {
    // «Последнее известное» допустимо только после хотя бы одного ответа.
    // Иначе это тот же случай «занятость уточняется» ниже.
    serveRuns([]);
    await mount();
    serveRuns(new Error("offline"));
    await act(async () => {
      await refreshChatRuns();
    });

    expect(container.querySelector("[data-agents-stale]")).not.toBeNull();
    expect(container.textContent).toContain("последнее известное состояние");
    // Состав всё равно виден: он пришёл и не устарел.
    expect(container.textContent).toContain("Корра");
    expect(button("Повторить")).toBeTruthy();
  });

  it.each([false, null] as const)(
    "до первого снимка с доступностью %s не выдаёт отсутствие активности за idle",
    async (reachable) => {
      $chatRuns.set([]);
      $chatRunsReachable.set(reachable);
      $chatRunsUpdatedAt.set(null);
      await mount("m", false);

      expect(container.textContent).toContain("Занятость уточняется");
      expect(container.querySelector("[data-agents-unknown]")).not.toBeNull();
      expect(container.textContent).toContain("Корра");
      expect(container.textContent).not.toContain("Готов к поручению");
      expect(container.textContent).not.toContain("Показано последнее известное состояние");
      expect(button("Повторить")).toBeTruthy();
    },
  );

  it("после настоящего пустого снимка показывает ноль и свободных агентов", async () => {
    serveRuns([]);
    await mount("s");

    expect(container.textContent).toContain("0");
    expect(container.textContent).toContain("никто не занят");
    expect(container.querySelector("[data-agents-live]")).not.toBeNull();
    expect(container.querySelector("[data-agents-unknown]")).toBeNull();
  });

  it("повтор после первого сбоя получает пустой снимок и только тогда объявляет idle", async () => {
    serveRuns([]);
    await mount("m", false);
    expect(container.querySelector("[data-agents-unknown]")).not.toBeNull();

    await click(button("Повторить"));
    await flush();

    expect(container.querySelector("[data-agents-live]")).not.toBeNull();
    expect(container.querySelector("[data-agents-unknown]")).toBeNull();
    expect(container.textContent).toContain("Готов к поручению");
  });

  it("при обрыве после снимка сохраняет последнее настоящее состояние", async () => {
    serveRuns([
      {
        message_id: "m1",
        session_id: "s-42",
        profile: "designer",
        status: "running",
        updated_at: 10,
        history_count: 1,
        user_message: { role: "user", content: "Собери презентацию" },
      },
    ]);
    await mount("m");
    await act(async () => {
      $chatRunsReachable.set(false);
    });

    expect(container.textContent).toContain("Дизайнер");
    expect(container.textContent).toContain("Работает");
    expect(container.querySelector("[data-agents-stale]")).not.toBeNull();
    expect(container.querySelector("[data-agents-unknown]")).toBeNull();
  });

  it("пустой ответ о составе не превращается в выдуманного агента", async () => {
    api.getProfiles.mockResolvedValue({ profiles: [] });
    await mount();

    expect(container.textContent).toContain("Агентов пока нет");
    expect(container.textContent).not.toContain("Корра");
    expect(links()).toEqual(["/profiles"]);
  });

  it("у каждого агента свой знак и слово состояния; свободный — с временем последней работы", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(FIXTURE_NOW * 1000);
    api.getProfiles.mockResolvedValue({
      profiles: [
        { name: "default", is_default: true, display_name: "Корра" },
        { name: "designer", is_default: false, display_name: "Дизайнер", distribution_name: "designer" },
      ],
    });
    // Сводка дашборда знает, когда главный агент работал в последний раз.
    $dashboardState.set(dashboardStateFixture());
    serveRuns([
      {
        message_id: "m1",
        session_id: "s-42",
        profile: "designer",
        status: "running",
        updated_at: FIXTURE_NOW - 10,
        history_count: 1,
        user_message: { role: "user", content: "Собери презентацию" },
      },
    ]);
    await mount("m");

    const glyphs = Array.from(container.querySelectorAll<HTMLElement>("[data-glyph]")).map(
      (node) => node.dataset.glyph,
    );
    expect(glyphs).toEqual(["design", "main"]);
    expect(container.textContent).toContain("Работает");
    expect(container.textContent).toContain("Собери презентацию");
    expect(container.textContent).toContain("Свободен");
    expect(container.textContent).toContain("Готов к поручению · был в работе сегодня в 12:20");
    $dashboardState.set(null);
    vi.useRealTimers();
  });

  describe("показывает столько строк, сколько помещается целиком", () => {
    // jsdom не раскладывает страницу, поэтому высоту окна списка задаём сами:
    // проверяем не вёрстку браузера, а само правило «строка либо целиком, либо
    // её нет» — из-за его отсутствия карточка и резала имя пополам.
    const setViewportHeight = (height: number) =>
      Object.defineProperty(HTMLDivElement.prototype, "clientHeight", {
        configurable: true,
        get: () => height,
      });

    afterEach(() => {
      Reflect.deleteProperty(HTMLDivElement.prototype, "clientHeight");
    });

    it("на двух целых строках показывает обоих агентов", async () => {
      setViewportHeight(100);
      await mount("m");

      expect(container.textContent).toContain("Корра");
      expect(container.textContent).toContain("Дизайнер");
      expect(container.textContent).not.toContain("ещё");
    });

    it("на одной строке показывает одного и честно считает остальных", async () => {
      setViewportHeight(50);
      await mount("m");

      expect(container.textContent).toContain("Корра");
      expect(container.textContent).not.toContain("Дизайнер");
      expect(container.textContent).toContain("ещё 1");
    });

    it("когда не помещается ни одна строка, остаётся счёт, а не половина имени", async () => {
      setViewportHeight(20);
      await mount("m");

      expect(container.textContent).not.toContain("Корра");
      expect(container.textContent).toContain("из 2 в работе");
    });
  });
});
