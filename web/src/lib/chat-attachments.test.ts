import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  MEDIA_DELIVERY_EXTS,
  chatUploadPath,
  shortName,
  splitAttachments,
  splitFileReferences,
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

describe("выдача файлов агента", () => {
  it("сводит MEDIA и ссылки на один файл в одну карточку", async () => {
    const { splitFileReferences } = await import("./chat-attachments");
    const path = "/opt/data/workspace/Отчёт (итог).xlsx";
    const result = splitFileReferences(`Готово\nMEDIA:"${path}"\n[Скачать](sandbox:${encodeURI(path)})\n[Файл](file://${encodeURI(path)})`);
    expect(result.paths).toEqual([path]);
    expect(result.text).toBe("Готово");
  });
  it("оставляет команды и внешние ссылки текстом, распознаёт отдельный путь", async () => {
    const { splitFileReferences } = await import("./chat-attachments");
    const result = splitFileReferences('```text\nMEDIA:/etc/example.txt\n```\n`/etc/example.txt`\n[Сайт](https://example.com/a.xlsx)\n/opt/data/workspace/a.xlsx');
    expect(result.paths).toEqual(["/opt/data/workspace/a.xlsx"]);
    expect(result.text).toContain("MEDIA:/etc/example.txt");
    expect(result.text).toContain("https://example.com/a.xlsx");
  });
  it("не превращает опасные и сетевые схемы в загрузку", async () => {
    const { splitFileReferences } = await import("./chat-attachments");
    for (const href of ["javascript:alert(1)", "file://host/path.xlsx", "//host/path.xlsx", "/workspace/../secret.xlsx"]) {
      expect(splitFileReferences(`[файл](${href})`).paths).toEqual([]);
    }
  });
});

describe("MEDIA с пробелами в пути (K21-345)", () => {
  const dir = "/opt/data/workspace/agents/0b8bf0d2e4679cad/results";
  const docx = `${dir}/Коммерческое предложение — блокноты и акриловые фигуры.docx`;
  const pdf = `${dir}/Коммерческое предложение — блокноты и акриловые фигуры.pdf`;

  it("два файла Марины на соседних строках дают два полных пути", () => {
    const result = splitFileReferences(`MEDIA:${docx}\nMEDIA:${pdf}`);
    expect(result.paths).toEqual([docx, pdf]);
    expect(result.text).toBe("");
  });
  it("два тега на одной строке дают два файла", () => {
    const result = splitFileReferences(`MEDIA:${docx} MEDIA:${pdf}`);
    expect(result.paths).toEqual([docx, pdf]);
    expect(result.text).toBe("");
  });
  it("склеенные теги без пробела не сливаются", () => {
    const result = splitFileReferences("MEDIA:/a/b c.pngMEDIA:/a/d e.png");
    expect(result.paths).toEqual(["/a/b c.png", "/a/d e.png"]);
  });
  it("точка и запятая в конце предложения не входят в путь", () => {
    expect(splitFileReferences(`Файл: MEDIA:${pdf}.`)).toEqual({ text: "Файл:", paths: [pdf] });
    expect(splitFileReferences(`MEDIA:${pdf}, готово`)).toEqual({ text: ", готово", paths: [pdf] });
    expect(splitFileReferences("MEDIA:/a/data.tar.gz.").paths).toEqual(["/a/data.tar.gz"]);
  });
  it("текст после пути без расширения в конце остаётся текстом", () => {
    const result = splitFileReferences("MEDIA:/a/b/отчёт за май.pdf вот он, проверьте");
    expect(result.paths).toEqual(["/a/b/отчёт за май.pdf"]);
    expect(result.text).toBe("вот он, проверьте");
  });
  it("кавычки, обратные кавычки и выделение работают", () => {
    expect(splitFileReferences(`MEDIA:"${pdf}"`).paths).toEqual([pdf]);
    expect(splitFileReferences(`MEDIA:'${pdf}'`).paths).toEqual([pdf]);
    expect(splitFileReferences(`\`MEDIA:${pdf}\``)).toEqual({ text: "", paths: [pdf] });
    expect(splitFileReferences(`**MEDIA:${pdf}**`)).toEqual({ text: "", paths: [pdf] });
    expect(splitFileReferences("MEDIA:/a/b.PDF").paths).toEqual(["/a/b.PDF"]);
  });
  it("путь без пробелов и неизвестное расширение разбираются как раньше", () => {
    expect(splitFileReferences("MEDIA:/a/b.pdf").paths).toEqual(["/a/b.pdf"]);
    expect(splitFileReferences("MEDIA:/a/script.py").paths).toEqual(["/a/script.py"]);
    expect(splitFileReferences("MEDIA:/a/report.pdf\nдальше текст").text).toBe("дальше текст");
  });
  it("код в блоке и инлайн-коде не разбирается", () => {
    const text = `\`\`\`\nMEDIA:${pdf}\n\`\`\`\nпример: \`/a/b c.pdf\``;
    const result = splitFileReferences(text);
    expect(result.paths).toEqual([]);
    expect(result.text).toBe(text);
  });
  it("список расширений совпадает с MEDIA_DELIVERY_EXTS сервера", () => {
    const source = readFileSync(new URL("../../../gateway/platforms/base.py", import.meta.url), "utf8");
    const body = /MEDIA_DELIVERY_EXTS: Tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/.exec(source)?.[1];
    expect(body).toBeTruthy();
    const server = [...(body ?? "").replace(/#[^\n]*/g, "").matchAll(/"\.([a-z0-9]+)"/g)].map(m => m[1]);
    expect([...MEDIA_DELIVERY_EXTS].sort()).toEqual(server.sort());
  });
});
