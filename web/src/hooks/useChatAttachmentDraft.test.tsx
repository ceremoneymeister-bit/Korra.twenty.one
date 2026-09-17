// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { restoreChatAttachmentDraft } from "./useChatAttachmentDraft";
import { readChatView } from "@/lib/chat-view-state";
import { MAX_ATTACHMENTS } from "@/lib/chat-attachments";

const file = (n: number) => ({ path: `/workspace/${n}.txt`, name: `${n}.txt`, size: 12, kind: "document", reader: "text" });
describe("restore attachments for review", () => {
  it("preserves the next draft and merges sent files without duplicates", () => {
    const key = "restore-attachments-merge";
    expect(restoreChatAttachmentDraft(key, [file(1)])).toBe(true);
    expect(restoreChatAttachmentDraft(key, [file(1), file(2)])).toBe(true);
    expect(JSON.parse(readChatView(`${key}:files`))).toEqual([file(1), file(2)]);
  });
  it("leaves the existing draft intact when restoration exceeds the limit", () => {
    const key = "restore-attachments-limit";
    const original = Array.from({ length: MAX_ATTACHMENTS }, (_, n) => file(n));
    expect(restoreChatAttachmentDraft(key, original)).toBe(true);
    expect(restoreChatAttachmentDraft(key, [file(100)])).toBe(false);
    expect(JSON.parse(readChatView(`${key}:files`))).toEqual(original);
  });
});
