import { describe, expect, it } from "vitest";

import { agentHref, agentRows, busyAgentCount } from "./dashboard-agents";
import type { ChatRun } from "./chat-runs";

const PROFILES = [
  { name: "default", is_default: true, display_name: "Корра" },
  { name: "designer", is_default: false, display_name: "Дизайнер" },
  { name: "lawyer", is_default: false, display_name: "Юрист" },
];

function run(over: Partial<ChatRun> & Pick<ChatRun, "profile">): ChatRun {
  return {
    message_id: `m-${over.profile}-${over.status ?? "completed"}`,
    session_id: `s-${over.profile}`,
    status: "completed",
    updated_at: 100,
    history_count: 1,
    user_message: { role: "user", content: "Сделай отчёт" },
    ...over,
  } as ChatRun;
}

describe("карточка «Агенты»", () => {
  it("без прочитанного состава строк нет — ноль не выдаётся за «все свободны»", () => {
    // Рабочая установка всегда отдаёт хотя бы `default`, поэтому пустой или
    // непонятный ответ значит «состав неизвестен», а не «агентов нет».
    expect(agentRows({ profiles: null, runs: [] })).toEqual([]);
    expect(agentRows({ profiles: [], runs: [] })).toEqual([]);
    expect(agentRows({ profiles: "нет", runs: [] })).toEqual([]);
  });

  it("свободный агент остаётся видимым и честно назван готовым", () => {
    const rows = agentRows({ profiles: PROFILES, runs: [] });
    expect(rows.map((row) => row.label)).toEqual(["Корра", "Дизайнер", "Юрист"]);
    expect(rows.every((row) => row.activity === "idle")).toBe(true);
    expect(rows[0].note).toBe("Готов к поручению");
    expect(rows[0].sessionId).toBeNull();
    expect(busyAgentCount(rows)).toBe(0);
  });

  it("до первого снимка сохраняет состав, но не придумывает состояние или переход к работе", () => {
    const rows = agentRows({
      profiles: PROFILES,
      runs: [run({ profile: "designer", status: "running", session_id: "s-running" })],
      activityKnown: false,
    });

    expect(rows.map((row) => row.label)).toEqual(["Корра", "Дизайнер", "Юрист"]);
    expect(rows.every((row) => row.activity === "unknown")).toBe(true);
    expect(rows.every((row) => row.note === "Состояние уточняется")).toBe(true);
    expect(rows.every((row) => row.sessionId === null && row.step === "")).toBe(true);
    expect(busyAgentCount(rows)).toBe(0);
  });

  it("сначала показывает тех, кому человек нужен прямо сейчас", () => {
    const rows = agentRows({
      profiles: PROFILES,
      runs: [
        run({ profile: "designer", status: "running", updated_at: 200 }),
        run({ profile: "lawyer", status: "waiting_decision", updated_at: 150 }),
      ],
    });
    expect(rows.map((row) => row.label)).toEqual(["Юрист", "Дизайнер", "Корра"]);
    expect(rows[0].note).toBe("Ждёт вашего решения");
    expect(rows[1].note).toBe("Работает");
    expect(busyAgentCount(rows)).toBe(2);
  });

  it("готовый непрочитанный ответ виден, прочитанный не притворяется событием", () => {
    const unread = agentRows({
      profiles: PROFILES,
      runs: [run({ profile: "designer", status: "completed", unread: true })],
    });
    expect(unread[0].label).toBe("Дизайнер");
    expect(unread[0].activity).toBe("ready");
    expect(unread[0].unread).toBe(true);

    const read = agentRows({
      profiles: PROFILES,
      runs: [run({ profile: "designer", status: "completed", unread: false })],
    });
    expect(read.find((row) => row.label === "Дизайнер")?.activity).toBe("idle");
  });

  it("у агента с несколькими работами выбирает самую важную", () => {
    const rows = agentRows({
      profiles: PROFILES,
      runs: [
        run({
          profile: "designer",
          status: "completed",
          unread: true,
          updated_at: 300,
          session_id: "s-done",
        }),
        run({
          profile: "designer",
          status: "waiting_decision",
          updated_at: 120,
          session_id: "s-decide",
        }),
      ],
    });
    const designer = rows.find((row) => row.label === "Дизайнер")!;
    expect(designer.activity).toBe("waiting");
    expect(designer.sessionId).toBe("s-decide");
  });

  it("шаг показывается только там, где работа идёт, и без переносов строк", () => {
    const rows = agentRows({
      profiles: PROFILES,
      runs: [
        run({
          profile: "designer",
          status: "running",
          user_message: { role: "user", content: " Собери\n  презентацию " },
        }),
      ],
    });
    expect(rows[0].step).toBe("Собери презентацию");
    expect(rows.find((row) => row.label === "Юрист")?.step).toBe("");
  });

  it("переход ведёт к нужному агенту и в тот самый чат", () => {
    // Снаружи главный агент известен серверным именем `default`, а его вкладка
    // в панели — пустой профиль.
    expect(agentHref({ profile: "", sessionId: null })).toBe("/agents?agent=default");
    expect(agentHref({ profile: "designer", sessionId: "s-7" })).toBe(
      "/agents?agent=designer&resume=s-7",
    );
  });
});
