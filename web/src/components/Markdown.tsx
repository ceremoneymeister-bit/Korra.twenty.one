import { useMemo, type ReactNode } from "react";
import { isTableDelimiter, splitTableRow } from "@/lib/markdown-tables";
import { cn } from "@/lib/utils";
import { CopyTextButton } from "./chat/CopyTextButton";
import { ScrollableTable } from "./chat/ScrollableTable";
import { FileAttachment } from "./chat/FileAttachment";
import { splitFileReferences } from "@/lib/chat-attachments";
import "./markdown.css";

/** Разметка ответа остаётся текстом до браузера. Здесь разбираем привычные
 * блоки ответа, не исполняя HTML и не меняя содержимое истории или Telegram.
 * Это ограниченный рендерер ответов, а не полный парсер CommonMark. */
export function Markdown({
  content,
  highlightTerms,
  streaming,
  className,
  variant = "message",
}: {
  content: string;
  highlightTerms?: string[];
  streaming?: boolean;
  className?: string;
  /**
   * `message` — реплика в чате: читаемая строка и заметные смысловые блоки.
   * `document` — файл, который человек открыл, чтобы прочитать: заголовки
   * должны быть видны как заголовки, иначе документ читается сплошняком.
   */
  variant?: "message" | "document";
}) {
  const files = useMemo(() => variant === "message" && !streaming
    ? splitFileReferences(content) : { text: content, paths: [] }, [content, streaming, variant]);
  const blocks = useMemo(() => parseBlocks(files.text), [files.text]);
  const caret = streaming ? <StreamingCaret /> : null;

  return (
    <div
      className={cn(
        "korra-markdown",
        variant === "document" && "korra-markdown--document",
        className,
      )}
    >
      {blocks.map((block, i) => (
        <Block
          key={i}
          block={block}
          highlightTerms={highlightTerms}
          caret={caret && i === blocks.length - 1 ? caret : null}
        />
      ))}
      {blocks.length === 0 && caret}
      {files.paths.map(path => <FileAttachment key={path} path={path} />)}
    </div>
  );
}

function StreamingCaret() {
  return (
    <span
      aria-hidden
      className="korra-markdown__caret inline-block w-[0.5em] h-[1em] ml-0.5 align-[-0.15em] bg-foreground/50 animate-pulse"
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type Alignment = "left" | "center" | "right";

type BlockNode =
  | { type: "code"; lang: string; content: string }
  | { type: "heading"; level: number; content: string }
  | { type: "hr" }
  | { type: "quote"; blocks: BlockNode[] }
  | { type: "list"; ordered: boolean; start: number; items: BlockNode[][] }
  | { type: "table"; header: string[]; rows: string[][]; align: Alignment[] }
  | { type: "paragraph"; content: string };

const LIST_MARKER = /^( *)([-*+]|\d+[.)])\s+(.*)$/;
const FENCE = /^ {0,3}(`{3,}|~{3,})([^`~]*)$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.+)/;
const QUOTE = /^ {0,3}> ?/;
const RULE = /^ {0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/;

function isBlockStart(lines: string[], i: number): boolean {
  const line = lines[i];
  return (
    FENCE.test(line) ||
    HEADING.test(line) ||
    QUOTE.test(line) ||
    RULE.test(line) ||
    LIST_MARKER.test(line) ||
    (line.includes("|") &&
      i + 1 < lines.length &&
      isTableDelimiter(lines[i + 1]))
  );
}

function parseBlocks(text: string, depth = 0): BlockNode[] {
  // Чрезмерная вложенность из внешнего текста не должна обрушить весь чат.
  if (depth >= 24) return [{ type: "paragraph", content: text }];
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: BlockNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i++;
      continue;
    }

    const fence = line.match(FENCE);
    if (fence) {
      const codeLines: string[] = [];
      const close = new RegExp(
        `^ {0,3}${fence[1][0]}{${fence[1].length},}\\s*$`,
      );
      i++;
      while (i < lines.length && !close.test(lines[i]))
        codeLines.push(lines[i++]);
      if (i < lines.length) i++;
      blocks.push({
        type: "code",
        lang: fence[2].trim(),
        content: codeLines.join("\n"),
      });
      continue;
    }

    const heading = line.match(HEADING);
    if (heading) {
      blocks.push({
        type: "heading",
        level: heading[1].length,
        content: heading[2],
      });
      i++;
      continue;
    }

    if (QUOTE.test(line)) {
      const quote: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i]))
        quote.push(lines[i++].replace(QUOTE, ""));
      blocks.push({
        type: "quote",
        blocks: parseBlocks(quote.join("\n"), depth + 1),
      });
      continue;
    }

    if (
      line.includes("|") &&
      i + 1 < lines.length &&
      isTableDelimiter(lines[i + 1])
    ) {
      const header = splitTableRow(line);
      const delimiters = splitTableRow(lines[i + 1]);
      const align = header.map((_, index): Alignment => {
        const delimiter = delimiters[index] ?? "";
        return delimiter.endsWith(":")
          ? delimiter.startsWith(":")
            ? "center"
            : "right"
          : "left";
      });
      i += 2;
      const rows: string[][] = [];
      while (
        i < lines.length &&
        lines[i].includes("|") &&
        lines[i].trim() !== ""
      ) {
        const cells = splitTableRow(lines[i++]);
        while (cells.length < header.length) cells.push("");
        rows.push(cells.slice(0, header.length));
      }
      blocks.push({ type: "table", header, rows, align });
      continue;
    }

    if (RULE.test(line)) {
      blocks.push({ type: "hr" });
      i++;
      continue;
    }

    const firstItem = line.match(LIST_MARKER);
    if (firstItem) {
      const indent = firstItem[1].length;
      const ordered = /^\d/.test(firstItem[2]);
      const items: BlockNode[][] = [];
      const isSibling = (match: RegExpMatchArray | null) =>
        Boolean(
          match &&
          match[1].length === indent &&
          /^\d/.test(match[2]) === ordered,
        );
      while (i < lines.length) {
        const marker = lines[i].match(LIST_MARKER);
        if (!marker || !isSibling(marker) || RULE.test(lines[i])) break;
        const contentIndent = lines[i].length - marker[3].length;
        const itemLines = [marker[3]];
        i++;
        while (i < lines.length) {
          if (lines[i].trim() === "") {
            let next = i + 1;
            while (next < lines.length && lines[next].trim() === "") next++;
            const nextLine = lines[next];
            if (nextLine === undefined) {
              i = next;
              break;
            }
            // Пустая строка внутри шага или между шагами не обрывает список.
            if (isSibling(nextLine.match(LIST_MARKER))) {
              i = next;
              break;
            }
            if (nextLine.search(/\S/) < contentIndent) break;
            itemLines.push("");
            i = next;
            continue;
          }
          if (lines[i].search(/\S/) >= contentIndent) {
            itemLines.push(lines[i++].slice(contentIndent));
          } else {
            if (isBlockStart(lines, i)) break;
            // Перенос абзаца без пустой строки остаётся в текущем пункте.
            itemLines.push(lines[i++].trimStart());
          }
        }
        items.push(parseBlocks(itemLines.join("\n"), depth + 1));
      }
      blocks.push({
        type: "list",
        ordered,
        start: ordered ? parseInt(firstItem[2], 10) : 1,
        items,
      });
      continue;
    }

    const paragraph = [lines[i++]];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !isBlockStart(lines, i)
    )
      paragraph.push(lines[i++]);
    blocks.push({ type: "paragraph", content: paragraph.join("\n") });
  }
  return blocks;
}

/* ------------------------------------------------------------------ */
/*  Block renderer                                                     */
/* ------------------------------------------------------------------ */

function Block({
  block,
  highlightTerms,
  caret,
}: {
  block: BlockNode;
  highlightTerms?: string[];
  caret?: ReactNode;
}) {
  switch (block.type) {
    case "code":
      return (
        <div className="korra-markdown__code">
          <div className="korra-markdown__code-header">
            <span className="korra-markdown__code-language">{block.lang || "Код"}</span>
            <CopyTextButton text={block.content} label="Скопировать код" />
          </div>
          <pre tabIndex={0} aria-label="Код">
            <code>
              {block.content}
              {caret}
            </code>
          </pre>
        </div>
      );

    case "heading": {
      const Tag = `h${block.level}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      return (
        <Tag>
          <InlineContent text={block.content} highlightTerms={highlightTerms} />
          {caret}
        </Tag>
      );
    }

    case "quote":
      return (
        <blockquote>
          {block.blocks.length === 0 && caret}
          {block.blocks.map((child, i) => (
            <Block
              key={i}
              block={child}
              highlightTerms={highlightTerms}
              caret={i === block.blocks.length - 1 ? caret : null}
            />
          ))}
        </blockquote>
      );

    case "table":
      return (
        <ScrollableTable>
          <table>
            <thead>
              <tr>
                {block.header.map((cell, index) => (
                  <th
                    key={index}
                    scope="col"
                    style={{ textAlign: block.align[index] }}
                  >
                    <InlineContent
                      text={cell}
                      highlightTerms={highlightTerms}
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td
                      key={cellIndex}
                      style={{ textAlign: block.align[cellIndex] }}
                    >
                      <InlineContent
                        text={cell}
                        highlightTerms={highlightTerms}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {caret}
        </ScrollableTable>
      );

    case "hr":
      return (
        <div className="korra-markdown__separator" role="separator">
          {caret}
        </div>
      );

    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag start={block.ordered ? block.start : undefined}>
          {block.items.map((item, i) => (
            <li key={i}>
              {item.length === 0 && i === block.items.length - 1 && caret}
              {item.map((child, j) => (
                <Block
                  key={j}
                  block={child}
                  highlightTerms={highlightTerms}
                  caret={
                    i === block.items.length - 1 && j === item.length - 1
                      ? caret
                      : null
                  }
                />
              ))}
            </li>
          ))}
        </Tag>
      );
    }

    case "paragraph":
      return (
        <p>
          <InlineContent text={block.content} highlightTerms={highlightTerms} />
          {caret}
        </p>
      );
  }
}

/* ------------------------------------------------------------------ */
/*  Inline parser + renderer                                           */
/* ------------------------------------------------------------------ */

type InlineNode =
  | { type: "text"; content: string }
  | { type: "code"; content: string }
  | { type: "bold"; content: string }
  | { type: "italic"; content: string }
  | { type: "link"; text: string; href: string }
  | { type: "br" };

function parseInline(text: string): InlineNode[] {
  const nodes: InlineNode[] = [];
  // Pattern priority: code > link > bold > italic > bare URL > line break
  const pattern =
    /(`[^`]+`)|(\[([^\]]+)\]\(([^)]+)\))|(\*\*([^*]+)\*\*)|(\*([^*]+)\*)|(\bhttps?:\/\/[^\s<>)\]]+)|(\n)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push({ type: "text", content: text.slice(lastIndex, match.index) });
    }

    if (match[1]) {
      // Inline code
      nodes.push({ type: "code", content: match[1].slice(1, -1) });
    } else if (match[2]) {
      // [text](url) link
      nodes.push({ type: "link", text: match[3], href: match[4] });
    } else if (match[5]) {
      // **bold**
      nodes.push({ type: "bold", content: match[6] });
    } else if (match[7]) {
      // *italic*
      nodes.push({ type: "italic", content: match[8] });
    } else if (match[9]) {
      // Bare URL
      nodes.push({ type: "link", text: match[9], href: match[9] });
    } else if (match[10]) {
      // Line break within paragraph
      nodes.push({ type: "br" });
    }

    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    nodes.push({ type: "text", content: text.slice(lastIndex) });
  }

  return nodes;
}

function InlineContent({
  text,
  highlightTerms,
}: {
  text: string;
  highlightTerms?: string[];
}) {
  const nodes = useMemo(() => parseInline(text), [text]);

  return (
    <>
      {nodes.map((node, i) => {
        switch (node.type) {
          case "text":
            return (
              <HighlightedText
                key={i}
                text={node.content}
                terms={highlightTerms}
              />
            );
          case "code":
            return (
              <code key={i} className="korra-markdown__inline-code">
                {node.content}
              </code>
            );
          case "bold":
            return (
              <strong key={i} className="font-semibold">
                <HighlightedText text={node.content} terms={highlightTerms} />
              </strong>
            );
          case "italic":
            return (
              <em key={i}>
                <HighlightedText text={node.content} terms={highlightTerms} />
              </em>
            );
          case "link": {
            // Security: only render http(s)/mailto links. Other schemes
            // (javascript:, data:, vbscript:) are dropped to plain text so a
            // crafted link in agent/message content can't execute on click.
            const href = node.href.trim();
            if (!/^(https?:|mailto:)/i.test(href)) {
              return (
                <HighlightedText
                  key={i}
                  text={node.text}
                  terms={highlightTerms}
                />
              );
            }
            return (
              <a
                key={i}
                href={href}
                target="_blank"
                rel="noreferrer"
                className="korra-markdown__link"
              >
                {node.text}
              </a>
            );
          }
          case "br":
            return <br key={i} />;
        }
      })}
    </>
  );
}

/** Highlight search terms within a plain text string. */
function HighlightedText({ text, terms }: { text: string; terms?: string[] }) {
  if (!terms || terms.length === 0) return <>{text}</>;

  // Build a regex that matches any of the search terms (case-insensitive)
  const escaped = terms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const regex = new RegExp(`(${escaped.join("|")})`, "gi");
  const parts = text.split(regex);

  return (
    <>
      {parts.map((part, i) =>
        regex.test(part) ? (
          <mark key={i} className="korra-markdown__highlight">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}
