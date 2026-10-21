// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchEffectDecisions,
  sendApprovalDecision,
} from "@/lib/chat-approvals";
import { EffectDecisionCenter } from "./EffectDecisionCenter";

vi.mock("@/lib/chat-approvals", () => ({
  fetchEffectDecisions: vi.fn(),
  sendApprovalDecision: vi.fn(),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function settle() {
  await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
}

async function click(label: string) {
  const button = [...container.querySelectorAll("button")].find((item) =>
    item.textContent?.includes(label),
  );
  if (!button) throw new Error(`Кнопка «${label}» не найдена`);
  await act(async () => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  vi.mocked(fetchEffectDecisions).mockResolvedValue([
    {
      request_id: "effect_pending",
      decision_kind: "outbound_message",
      effect_status: "pending",
      source_session_id: "chat-b",
      command: "Кому: telegram:2002\n\nТретий черновик",
      choices: ["once", "deny"],
    },
    {
      request_id: "effect_sent",
      decision_kind: "outbound_message",
      effect_status: "succeeded",
      source_session_id: "chat-a",
      command: "Кому: email:a@example.test\n\nПервый черновик",
      choices: ["once", "deny"],
    },
    {
      request_id: "effect_denied",
      decision_kind: "outbound_message",
      effect_status: "denied",
      source_session_id: "chat-a",
      command: "Кому: slack:C1\n\nВторой черновик",
      choices: ["once", "deny"],
    },
  ]);
  vi.mocked(sendApprovalDecision).mockResolvedValue({
    ok: true,
    expired: false,
    error: "",
    effectStatus: "succeeded",
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  delete window.__KORRA_OWNER_TIMEZONE__;
});

it("центр сохраняет дату создания pending, старой очереди и истории", async () => {
  vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-19T05:30:00Z"));
  window.__KORRA_OWNER_TIMEZONE__ = "Asia/Novosibirsk";
  const timestamp = Date.parse("2026-10-03T11:49:00Z") / 1000;
  vi.mocked(fetchEffectDecisions).mockResolvedValue(
    (["pending", "needs_review", "denied"] as const).map(effect_status => ({
      request_id: effect_status, decision_kind: "outbound_message", effect_status,
      source_session_id: "chat-b", timestamp, updated_at: Date.now() / 1000,
      command: "Кому: k21b-no-transport\nТестовый черновик", choices: ["once", "deny"],
    })),
  );
  await act(async () => root.render(<EffectDecisionCenter />));
  await settle();
  await click("Решения");
  expect([...container.querySelectorAll("time")].map(time => time.textContent)).toEqual(
    Array(3).fill("03.10.2026 в 18:49"),
  );
  expect(container.querySelectorAll('[role="note"]')).toHaveLength(1);
});

describe("EffectDecisionCenter", () => {
  it("показывает ожидающее действие из другого чата и компактную историю", async () => {
    await act(async () => {
      root.render(<EffectDecisionCenter profile="sales" currentSessionId="chat-a" />);
    });
    await settle();

    expect(container.textContent).toContain("1 ждёт");
    await click("Решения");

    expect(container.textContent).toContain("Третий черновик");
    expect(container.textContent).toContain("Отправить");
    expect(container.textContent).not.toContain("до конца чата");
    expect(container.querySelectorAll("details")).toHaveLength(2);
    expect(container.textContent).toContain("Выполнено · Кому: email:a@example.test");
    expect(container.textContent).toContain("Отклонено · Кому: slack:C1");

    await click("Отправить");
    expect(sendApprovalDecision).toHaveBeenCalledWith({
      sessionId: "chat-b",
      requestId: "effect_pending",
      choice: "once",
      profile: "sales",
    });
  });

  it("решение текущего чата проводит через его локальное состояние", async () => {
    const onCurrentDecision = vi.fn(async () => true);
    vi.mocked(fetchEffectDecisions).mockResolvedValue([
      {
        request_id: "effect_here",
        decision_kind: "payment",
        effect_status: "pending",
        source_session_id: "chat-a",
        command: "Кому: ООО Тест\nСумма: 100 RUB",
        choices: ["once", "deny"],
      },
    ]);
    await act(async () => {
      root.render(
        <EffectDecisionCenter
          currentSessionId="chat-a"
          onCurrentDecision={onCurrentDecision}
        />,
      );
    });
    await settle();
    await click("Решения");
    expect(container.textContent).toContain("Проверьте оплату");
    await click("Не оплачивать");
    expect(onCurrentDecision).toHaveBeenCalledWith("effect_here", "deny");
    expect(sendApprovalDecision).not.toHaveBeenCalled();
  });

  it("после ошибки локального решения не оставляет карточку навечно занятой", async () => {
    const onCurrentDecision = vi.fn(async () => {
      throw new Error("connection lost");
    });
    vi.mocked(fetchEffectDecisions).mockResolvedValue([
      {
        request_id: "effect_here",
        decision_kind: "outbound_message",
        effect_status: "pending",
        source_session_id: "chat-a",
        command: "Кому: telegram:2002\n\nЧерновик",
        choices: ["once", "deny"],
      },
    ]);
    await act(async () => {
      root.render(
        <EffectDecisionCenter
          currentSessionId="chat-a"
          onCurrentDecision={onCurrentDecision}
        />,
      );
    });
    await settle();
    await click("Решения");
    await click("Не отправлять");

    expect(container.textContent).toContain("Не удалось передать решение");
    const deny = [...container.querySelectorAll("button")].find((item) =>
      item.textContent?.includes("Не отправлять"),
    );
    expect(deny?.disabled).toBe(false);
  });
});

it("старые результаты можно закрыть без отправки, истёкшие доступны только в истории", async () => {
  vi.mocked(fetchEffectDecisions).mockResolvedValue([
    { request_id: "legacy", effect_status: "needs_review", source_session_id: "old", command: "Кому: telegram:42\nСтарый отчёт", choices: ["deny"] },
    { request_id: "expired", effect_status: "expired", source_session_id: "old", command: "Кому: telegram:42\nИстёкший отчёт", choices: [] },
  ]);
  await act(async () => root.render(<EffectDecisionCenter profile="sales" />));
  await settle();
  expect(container.textContent).toContain("нет ожидающих");
  await click("Решения");
  expect(container.textContent).toContain("Срок отправки истёк");
  expect([...container.querySelectorAll("button")].some((b) => b.textContent === "Отправить")).toBe(false);
  await click("Закрыть без отправки");
  expect(sendApprovalDecision).toHaveBeenCalledWith({ sessionId: "old", requestId: "legacy", choice: "deny", profile: "sales" });
});


it("диплинк сразу раскрывает решение и объясняет потерянный исходный чат", async () => {
  vi.mocked(fetchEffectDecisions).mockResolvedValue([{
    request_id: "effect_missing", decision_kind: "outbound_message", effect_status: "pending",
    source_session_id: "deleted-chat", command: "Тестовый черновик",
    decision_fallback: true, decision_reason: "Исходный чат удалён. Ответьте здесь.",
  }]);
  await act(async () => root.render(<EffectDecisionCenter profile="sales" expandRequest={1} />));
  await settle();
  expect(container.querySelector("button")?.getAttribute("aria-expanded")).toBe("true");
  expect(container.textContent).toContain("Исходный чат удалён. Ответьте здесь.");
  await click("Не отправлять");
  expect(sendApprovalDecision).toHaveBeenCalledWith({ sessionId: "deleted-chat", requestId: "effect_missing", choice: "deny", profile: "sales" });
});


it.each(["pending", "needs_review"] as const)("не предлагает ответ для %s с неоднозначным ledger", async (status) => {
  vi.mocked(fetchEffectDecisions).mockResolvedValue([{
    request_id: "effect_blocked", decision_kind: "outbound_message", effect_status: status,
    source_session_id: "old-chat", command: "Сохранённый черновик", choices: [],
    decision_fallback: true, decision_reason: "Найдено несколько записей решения. Нужна проверка.",
  }]);
  await act(async () => root.render(<EffectDecisionCenter profile="sales" expandRequest={1} />));
  await settle();
  if (status === "needs_review") container.querySelector("details")!.open = true;
  expect(container.textContent).toContain("Найдено несколько записей решения. Нужна проверка.");
  expect(container.textContent).not.toContain("Закрыть без отправки");
  expect(container.textContent).not.toContain("Не отправлять");
  expect(container.textContent).not.toContain("Отправить");
  expect(sendApprovalDecision).not.toHaveBeenCalled();
});

describe("EffectDecisionCenter в скрытой вкладке (UX 17.10)", () => {
  it("не опрашивает сервер, пока вкладка скрыта, и обновляется при возврате", async () => {
    vi.useFakeTimers();
    const setHidden = (hidden: boolean) => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
      document.dispatchEvent(new Event("visibilitychange"));
    };
    try {
      await act(async () => root.render(<EffectDecisionCenter profile="" active currentSessionId={null} />));
      const calls = () => vi.mocked(fetchEffectDecisions).mock.calls.length;
      const initial = calls();
      setHidden(true);
      await act(async () => { vi.advanceTimersByTime(30_000); });
      expect(calls()).toBe(initial);
      await act(async () => { setHidden(false); });
      expect(calls()).toBe(initial + 1);
    } finally {
      setHidden(false);
      vi.useRealTimers();
    }
  });
});
