import { describe, expect, it } from "vitest";

import {
  activityCount,
  agentMonogram,
  attentionOf,
  buildAgentStatuses,
  groupByAttention,
  pickRail,
  railSlots,
  statusSpeech,
  strongestAttention,
} from "./agent-status";
import type { ChatRun } from "./chat-runs";

function run(profile: string, status: ChatRun["status"], extra: Partial<ChatRun> = {}): ChatRun {
  return {
    message_id: `${profile}-${status}-${extra.session_id ?? "s"}`,
    session_id: "s",
    profile,
    status,
    updated_at: 100,
    history_count: 0,
    user_message: { role: "user", content: "задача" },
    ...extra,
  };
}

const TABS = ["", "assistant", "designer", "bitrix", "docs", "finance", "management", "rop", "lawyer"].map(
  (profile) => ({ profile, label: profile || "Нюра" }),
);

describe("состояния агентов", () => {
  it("решения, работа, очередь, ответ и сбой — разные поля, число только у решений", () => {
    const statuses = buildAgentStatuses(
      TABS.map((tab) => tab.profile),
      [
        run("designer", "running"),
        run("rop", "queued"),
        run("bitrix", "waiting_decision", { pending_decisions: 4 }),
        run("bitrix", "waiting_decision", { session_id: "other" }),
      ],
      [run("docs", "completed")],
      [run("finance", "failed")],
    );
    expect(statuses.get("designer")).toMatchObject({ running: 1, decisions: 0 });
    expect(statuses.get("rop")).toMatchObject({ queued: 1, running: 0 });
    // Старый сервер не знает pending_decisions: строка считается за одно.
    expect(statuses.get("bitrix")?.decisions).toBe(5);
    expect(statuses.get("docs")?.unread).toBe(true);
    expect(statuses.get("finance")?.error).toBe(true);
    expect(statuses.get("")).toMatchObject({ running: 0, decisions: 0, unread: false, error: false });

    expect(attentionOf(statuses.get("bitrix"))).toBe("decision");
    expect(attentionOf(statuses.get("finance"))).toBe("error");
    expect(attentionOf(statuses.get("docs"))).toBe("unread");
    // «Работает» не требует человека — это не внимание, а кольцо.
    expect(attentionOf(statuses.get("designer"))).toBeNull();
    // Ход, очередь и агент с решениями — три работы, а не пять решений.
    expect(activityCount(statuses.values())).toBe(3);
    expect(strongestAttention(statuses.values())).toEqual({ kind: "decision", count: 3 });
    expect(statusSpeech(statuses.get("bitrix"))).toBe(". ждёт 5 решений");
  });

  it("монограмма берёт отличающую часть имени", () => {
    expect(agentMonogram("Нюра | Bitrix")).toBe("Bi");
    expect(agentMonogram("Нюра | Документы")).toBe("До");
    expect(agentMonogram("Нюра | РОП")).toBe("РОП");
    expect(agentMonogram("Нюра")).toBe("Н");
    expect(agentMonogram("Учитель китайского")).toBe("УК");
    expect(agentMonogram("  ")).toBe("·");
  });

  it("в полосу помещается 4 аватара на 320, 5 на 375–390 и 6 на 430 без прокрутки", () => {
    expect(railSlots(320)).toBe(4);
    expect(railSlots(375)).toBe(5);
    expect(railSlots(390)).toBe(5);
    expect(railSlots(430)).toBe(6);
  });

  it("в полосе открытый, закреплённые, ждущие вас, работающие, недавние — в порядке пользователя", () => {
    const statuses = buildAgentStatuses(
      TABS.map((tab) => tab.profile),
      [run("designer", "running"), run("rop", "queued"), run("bitrix", "waiting_decision")],
      [run("docs", "completed")],
      [run("finance", "failed")],
    );
    const lastActive = new Map([["lawyer", 900], ["management", 800], ["assistant", 10]]);
    const pick = pickRail(TABS, statuses, { active: "", pinned: ["assistant"], lastActive, slots: 5 });
    // Открытый (главный), закреплённый, три ждущих; работающий не влез.
    expect(pick.shown.map((tab) => tab.profile)).toEqual(["", "assistant", "bitrix", "docs", "finance"]);
    expect(pick.hiddenAttention).toBeNull();

    const narrow = pickRail(TABS, statuses, { active: "lawyer", lastActive, slots: 3 });
    expect(narrow.shown.map((tab) => tab.profile)).toEqual(["bitrix", "finance", "lawyer"]);
    // Не влезший «Документы» с новым ответом — точка на «Все агенты».
    expect(narrow.hiddenAttention).toBe("unread");

    const calm = pickRail(TABS, buildAgentStatuses([], []), { active: "docs", lastActive, slots: 3 });
    expect(calm.shown.map((tab) => tab.profile)).toEqual(["docs", "management", "lawyer"]);
  });

  it("список агентов ставит ждущих вас сверху по срочности", () => {
    const statuses = buildAgentStatuses(
      TABS.map((tab) => tab.profile),
      [run("bitrix", "waiting_decision")],
      [run("docs", "completed")],
      [run("finance", "failed")],
    );
    const { waiting, rest } = groupByAttention(TABS, statuses);
    expect(waiting.map((tab) => tab.profile)).toEqual(["bitrix", "finance", "docs"]);
    expect(rest).toHaveLength(6);
  });
});
