import { describe, expect, it } from "vitest";

import {
  chatUploadPath,
  shortName,
  splitAttachments,
  toDisplay,
} from "./chat-attachments";

const BLOCK = `[вложения]
1. Отчёт КЕДР.md · md · 197 Б · читать: read_file
   /opt/data/home/client/inbox/2026-08-19/abc-Otchet-KEDR.md
2. Презентация.pptx · pptx · 63 КБ · читать: powerpoint
   /opt/data/home/client/inbox/2026-08-19/def-Prezentaciya.pptx`;

describe("splitAttachments", () => {
  it("keeps the human text and hides the filesystem path", () => {
    const { text, attachments } = splitAttachments(`разбери оба файла\n\n${BLOCK}`);

    expect(text).toBe("разбери оба файла");
    expect(attachments).toHaveLength(2);
    expect(attachments[0]).toMatchObject({
      name: "Отчёт КЕДР.md",
      kind: "md",
      sizeLabel: "197 Б",
    });
    expect(attachments[1].kind).toBe("pptx");
    // The path is the model's business; it must not reach the transcript.
    expect(text).not.toContain("/opt/data");
  });

  it("handles a message that is only an attachment", () => {
    const { text, attachments } = splitAttachments(BLOCK);

    expect(text).toBe("");
    expect(attachments).toHaveLength(2);
  });

  it("leaves ordinary messages untouched", () => {
    const { text, attachments } = splitAttachments("просто текст");

    expect(text).toBe("просто текст");
    expect(attachments).toHaveLength(0);
  });

  it("shows the raw text rather than swallowing an unparsable block", () => {
    const broken = "смотри\n\n[вложения]\nчто-то не то";
    const { text, attachments } = splitAttachments(broken);

    expect(text).toBe(broken);
    expect(attachments).toHaveLength(0);
  });
});

describe("toDisplay", () => {
  it("formats sizes for the card caption", () => {
    const [card] = toDisplay([
      { path: "/p/a.pdf", name: "a.pdf", kind: "pdf", size: 64483, reader: "pdf" },
    ]);

    expect(card).toEqual({
      key: "/p/a.pdf",
      name: "a.pdf",
      kind: "pdf",
      sizeLabel: "63 КБ",
    });
  });
});

describe("shortName", () => {
  it("truncates in the middle so the extension stays visible", () => {
    const out = shortName("Презентация пульт управления и AI агенты.pptx");

    expect(out.endsWith(".pptx")).toBe(true);
    expect(out.length).toBeLessThanOrEqual(29);
  });

  it("leaves short names alone", () => {
    expect(shortName("a.pdf")).toBe("a.pdf");
  });
});

describe("chatUploadPath", () => {
  it("scopes uploads to the same profile as completions", () => {
    expect(chatUploadPath("research & data"))
      .toBe("/api/chat/upload?profile=research%20%26%20data");
    expect(chatUploadPath()).toBe("/api/chat/upload");
  });
});
