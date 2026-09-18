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
