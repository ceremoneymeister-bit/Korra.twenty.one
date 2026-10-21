// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { EffectDecisionCenter } from "./EffectDecisionCenter";
import { AgentRunBadge, SessionRunActivity } from "./SessionRunActivity";
import { refreshChatRuns } from "@/lib/chat-runs";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => vi.unstubAllGlobals());

it("недоступный центр решений показывает неизвестность и восстанавливается после опроса", async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 503 }));
  vi.stubGlobal("fetch", fetcher);
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<EffectDecisionCenter profile="blocked" hideWhenSettledOnPhone />));
    expect(container.textContent).toContain("Решения этого профиля временно недоступны");
    expect(container.textContent).not.toContain("нет ожидающих");
    expect(container.querySelector("section")?.className).not.toContain("max-lg:hidden");
    fetcher.mockImplementation(async () => Response.json({ data: [] }));
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(container.textContent).toBe("");
  } finally {
    await act(async () => root.unmount()); container.remove(); vi.useRealTimers();
  }
});

it("частичный опрос сохраняет доступную работу и явно называет недоступного агента", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ runs: [{
    message_id: "healthy-work", session_id: "healthy-chat", profile: "healthy", status: "running",
    updated_at: 1, user_message: { role: "user", content: "Проверка" },
  }], unavailable_profiles: ["blocked"] })));
  await refreshChatRuns();
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<MemoryRouter>
      <AgentRunBadge profile="blocked" label="Проблемный" />
      <SessionRunActivity tabs={[{ profile: "healthy", label: "Исправный" }, { profile: "blocked", label: "Проблемный" }]} />
    </MemoryRouter>));
    expect(container.textContent).toContain("Исправный");
    expect(container.querySelector('a[href*="healthy-chat"]')).not.toBeNull();
    expect(container.textContent).toContain("Недоступны профили: Проблемный");
    expect(container.textContent).toContain("Статус требует проверки");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ runs: [], unavailable_profiles: [] })));
    await act(async () => refreshChatRuns());
    expect(container.textContent).not.toContain("Недоступны профили");
  } finally {
    await act(async () => root.unmount()); container.remove();
  }
});

it("пустая доступная часть решений не выдаётся за отсутствие ожидающих", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ data: [], unavailable_profiles: ["blocked"] })));
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<EffectDecisionCenter profile="healthy" hideWhenSettledOnPhone />));
    expect(container.textContent).toContain("Недоступны профили: blocked");
    expect(container.textContent).toContain("Список решений неполный");
    expect(container.textContent).not.toContain("нет ожидающих");
    expect(container.querySelector("section")?.className).not.toContain("max-lg:hidden");
  } finally {
    await act(async () => root.unmount()); container.remove();
  }
});
