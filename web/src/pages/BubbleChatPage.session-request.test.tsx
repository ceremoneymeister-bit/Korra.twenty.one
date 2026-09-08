// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const chat = vi.hoisted(() => ({
  sessionId: "ordinary-session" as string | null,
  isStreaming: false,
  loadSession: vi.fn(async () => {}),
  reset: vi.fn(),
  send: vi.fn(async () => true),
}));

vi.mock("@/hooks/useChatStream", () => ({
  useChatStream: () => ({
    messages: [],
    sessionId: chat.sessionId,
    isStreaming: chat.isStreaming,
    error: null,
    approvals: [],
    send: chat.send,
    resolveApproval: vi.fn(async () => true),
    retryPending: vi.fn(async () => true),
    discardPending: vi.fn(),
    abort: vi.fn(),
    loadSession: chat.loadSession,
    reset: chat.reset,
  }),
}));
vi.mock("@/hooks/useSessionList", () => ({
  useSessionList: () => ({
    sessions: [], loading: false, error: null, refresh: vi.fn(async () => {}),
  }),
}));
vi.mock("@/hooks/useConfirmDelete", () => ({
  useConfirmDelete: () => ({
    cancel: vi.fn(), confirm: vi.fn(), isDeleting: false, isOpen: false,
    pendingId: null, requestDelete: vi.fn(),
  }),
}));
vi.mock("@/hooks/useDictation", () => ({
  useDictation: () => ({
    state: "idle", available: false, unavailableReason: "test", start: vi.fn(),
    stop: vi.fn(), cancel: vi.fn(),
  }),
}));
vi.mock("@/contexts/useProfileScope", () => ({
  useProfileScope: () => ({ profiles: [] }),
}));
vi.mock("@/themes", () => ({ useTheme: () => ({ themeName: "light" }) }));
vi.mock("@/components/chat/TranscriptViewport", () => ({
  TranscriptViewport: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("thinking-orbs", () => ({
  ThinkingOrb: () => <span />,
}));

import BubbleChatPage from "./BubbleChatPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function render(props: React.ComponentProps<typeof BubbleChatPage>) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <BubbleChatPage {...props} />
      </MemoryRouter>,
    );
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  chat.sessionId = "ordinary-session";
  chat.isStreaming = false;
  chat.loadSession.mockClear();
  chat.reset.mockClear();
  chat.send.mockClear();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("BubbleChatPage owner-controlled session", () => {
  it("defers a requested session until the existing stream finishes and reloads on refreshKey", async () => {
    const request = { sessionId: `intake_${"a".repeat(40)}`, refreshKey: 0 };
    chat.isStreaming = true;
    await render({ agentProfile: "", sessionRequest: request });
    expect(chat.loadSession).not.toHaveBeenCalled();

    chat.isStreaming = false;
    await render({ agentProfile: "", sessionRequest: request });
    expect(chat.loadSession).toHaveBeenCalledTimes(1);
    expect(chat.loadSession).toHaveBeenLastCalledWith(request.sessionId);
    expect(container.querySelector<HTMLTextAreaElement>("textarea")?.disabled).toBe(true);

    chat.sessionId = request.sessionId;
    await render({ agentProfile: "", sessionRequest: request });
    expect(container.querySelector<HTMLTextAreaElement>("textarea")?.disabled).toBe(false);

    await render({ agentProfile: "", sessionRequest: request });
    expect(chat.loadSession).toHaveBeenCalledTimes(1);
    await render({
      agentProfile: "",
      sessionRequest: { ...request, refreshKey: 1 },
    });
    expect(chat.loadSession).toHaveBeenCalledTimes(2);
  });

  it("keeps the guard scoped to the bound session and restores the same request after dismissal", async () => {
    const bound = `intake_${"b".repeat(40)}`;
    chat.sessionId = bound;
    await render({
      agentProfile: "",
      sessionRequest: { sessionId: bound, refreshKey: 0 },
      sessionGuard: { sessionId: bound, locked: true },
    });
    expect(container.querySelector<HTMLTextAreaElement>("textarea")?.disabled).toBe(true);

    chat.sessionId = "ordinary-session";
    await render({
      agentProfile: "",
      sessionRequest: null,
      sessionGuard: { sessionId: bound, locked: true },
    });
    expect(container.querySelector<HTMLTextAreaElement>("textarea")?.disabled).toBe(false);

    chat.sessionId = bound;
    await render({
      agentProfile: "",
      sessionRequest: { sessionId: bound, refreshKey: 0 },
      sessionGuard: { sessionId: bound, locked: true },
    });
    expect(chat.loadSession).toHaveBeenCalledTimes(2);
    expect(container.querySelector<HTMLTextAreaElement>("textarea")?.disabled).toBe(true);
  });
});
