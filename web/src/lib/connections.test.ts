import { describe, expect, it } from "vitest";

import type { ConnectionsGoogleProfile } from "@/lib/api";
import {
  borrowStatus,
  borrowStatusText,
  capabilityNote,
  defaultConnectTarget,
  googleSources,
  profileLabel,
  serviceLabels,
  sharingState,
  toggledSharing,
} from "./connections";

function row(profile: string, over: Partial<ConnectionsGoogleProfile> = {}): ConnectionsGoogleProfile {
  return {
    profile,
    label: "",
    access: "none",
    state: "not_connected",
    services: [],
    pending: false,
    tools: { calendar: true, workspace_skill: true },
    ...over,
  };
}

const main = row("default", { access: "own", state: "connected", services: ["drive", "calendar"], shared_with: ["designer"] });
const designer = row("designer", {
  label: "Дизайнер",
  access: "shared",
  state: "connected",
  services: ["drive", "calendar"],
  shared_from: "default",
  tools: { calendar: true, workspace_skill: false },
});
const lawyer = row("lawyer", { label: "Юрист" });
const mentor = row("mentor", { pending: true });
const smm = row("smm", { access: "own", state: "connected", services: ["email"] });
const reels = row("reels", { access: "shared", state: "connected", shared_from: "smm", services: ["email"] });
const rows = [main, designer, lawyer, mentor, smm, reels];

describe("общий доступ к подключению Google", () => {
  it("находит источники и подписывает агентов", () => {
    expect(googleSources(rows).map((item) => item.profile)).toEqual(["default", "smm"]);
    expect(profileLabel(main)).toBe("Главный агент");
    expect(profileLabel(designer)).toBe("Дизайнер");
    expect(serviceLabels(["drive", "calendar", "email"])).toEqual(["Календарь", "Почта", "Диск"]);
  });

  it("не предлагает того, что сервер отклонит", () => {
    expect(borrowStatus(main, main)).toEqual({ kind: "source" });
    expect(borrowStatus(designer, main)).toEqual({ kind: "shared" });
    expect(borrowStatus(lawyer, main)).toEqual({ kind: "available" });
    expect(borrowStatus(mentor, main)).toEqual({ kind: "pending" });
    expect(borrowStatus(smm, main)).toEqual({ kind: "own" });
    expect(borrowStatus(reels, main)).toEqual({ kind: "other", source: "smm" });
    expect(borrowStatus(main, smm)).toEqual({ kind: "lender" });
    expect(borrowStatusText({ kind: "other", source: "smm" }, () => "SMM")).toBe(
      "Пользуется подключением агента «SMM»",
    );
  });

  it("«всем агентам» — это все, кому можно, и включается одним составом", () => {
    const state = sharingState(rows, main);
    expect(state).toEqual({ eligible: ["designer", "lawyer"], shared: ["designer"], all: false });
    expect(toggledSharing(state.shared, "lawyer", true)).toEqual(["designer", "lawyer"]);
    expect(toggledSharing(["designer", "lawyer"], "designer", false)).toEqual(["lawyer"]);
    const everyone = sharingState([main, { ...designer }, { ...lawyer, access: "shared", shared_from: "default" }], main);
    expect(everyone.all).toBe(true);
    expect(sharingState([main, smm], main)).toEqual({ eligible: [], shared: [], all: false });
  });

  it("честно говорит, что готовому агенту без навыка доступен только календарь", () => {
    expect(capabilityNote(designer, ["drive", "calendar"])).toBe(
      "Календарь доступен сразу; диск — после установки навыка Google Workspace.",
    );
    expect(capabilityNote(designer, ["calendar"])).toBeNull();
    expect(capabilityNote(main, ["drive", "calendar"])).toBeNull();
    expect(capabilityNote(designer, ["email"])).toBe("Почта — после установки навыка Google Workspace.");
  });

  it("предлагает подключить тому, кого назвали, иначе начатому или главному", () => {
    expect(defaultConnectTarget(rows, "lawyer")).toBe("lawyer");
    expect(defaultConnectTarget(rows, null)).toBe("mentor");
    expect(defaultConnectTarget([row("default"), lawyer], null)).toBe("default");
    expect(defaultConnectTarget([main, designer], null)).toBeNull();
  });
});
