// @vitest-environment jsdom

/**
 * Возврат к чату из соседней вкладки — со стороны того, что видит человек.
 *
 * Хук проверяется отдельно (`hooks/useChatStream.test.tsx`); здесь важна цена
 * возврата в карточках вложений: показанная картинка не должна исчезать в
 * «Проверяем вложение…» и качаться заново. Считаем ровно те запросы, которые
 * делает карточка, — `/api/files/attachment` (метаданные) и
 * `/api/files/download` (байты превью), — за одинаковые циклы ухода и возврата.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { AttachmentCardList } from "@/components/ChatAttachments";
import { Markdown } from "@/components/Markdown";
import { splitAttachments } from "@/lib/chat-attachments";
import { chatViewKey } from "@/lib/chat-view-state";
import { useChatStream } from "@/hooks/useChatStream";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PHOTO = "/opt/data/workspace/фото.png";
const ANSWER = "/opt/data/workspace/ответ.png";
const SESSION = "s-photos";
const HISTORY = [
  {
    role: "user",
    content: `Вот фото\n\n[вложения]\n1. фото.png · png · 12 КБ · читать: read_file\n${PHOTO}`,
  },
  { role: "assistant", content: `Готово.\nMEDIA:${ANSWER}` },
];

/** Лента ровно в том виде, в каком её собирает BubbleChatTranscript:
 *  вложения владельца карточками, вложения ответа — из разметки. */
function Transcript() {
  const chat = useChatStream();
  return (
    <div>
      {chat.messages.map(message => (
        <div key={message.id}>
          <AttachmentCardList
            items={message.attachments ?? splitAttachments(message.content).attachments}
          />
          {message.role === "assistant" && <Markdown content={message.content} />}
        </div>
      ))}
    </div>
  );
}

let container: HTMLDivElement;
let root: Root;
let requests: string[];
let revoked: string[];
const objectUrls = URL as unknown as {
  createObjectURL: (blob: Blob) => string;
  revokeObjectURL: (value: string) => void;
};

function fileRequests(): string[] {
  return requests.filter(url => url.includes("/api/files/"));
}

beforeEach(async () => {
  localStorage.clear();
  sessionStorage.clear();
  requests = [];
  revoked = [];
  window.__HERMES_SESSION_TOKEN__ = "test-token";
  // jsdom не умеет object URL — подменяем только две статические функции,
  // сам конструктор URL нужен рабочим (по нему разбираются адреса запросов).
  let blobs = 0;
  objectUrls.createObjectURL = () => `blob:preview-${++blobs}`;
  objectUrls.revokeObjectURL = (value: string) => revoked.push(value);
  vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL) => {
    const href = String(url);
    requests.push(href);
    if (href.includes("/api/files/attachment")) {
      const path = new URL(href, "https://local.test").searchParams.get("path") ?? "";
      // Настоящая сеть отвечает не в том же микротаске — иначе очистка ленты
      // успела бы схлопнуться и мигание из проверки бы исчезло.
      await new Promise(resolve => setTimeout(resolve, 1));
      return new Response(JSON.stringify({
        path, name: path.split("/").pop(), kind: "png", size: 1024, reader: "image",
      }), { headers: { "content-type": "application/json" } });
    }
    if (href.includes("/api/files/download")) {
      await new Promise(resolve => setTimeout(resolve, 1));
      return new Response(new Blob(["картинка"]), { headers: { "content-type": "image/png" } });
    }
    if (href.includes("/api/chat/runs")) {
      return new Response(JSON.stringify({ runs: [] }), { headers: { "content-type": "application/json" } });
    }
    if (href.includes("/messages")) {
      await new Promise(resolve => setTimeout(resolve, 1));
      return new Response(JSON.stringify({ session_id: SESSION, messages: HISTORY }), {
        headers: { "content-type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ data: [] }), { headers: { "content-type": "application/json" } });
  }));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  // Тот же выбранный чат, что и после обычной перезагрузки страницы.
  sessionStorage.setItem(`${chatViewKey()}:selected`, SESSION);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  delete window.__HERMES_SESSION_TOKEN__;
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Дать ленте дойти до нужного состояния: загрузка истории, метаданные и
 *  байты каждой картинки — это несколько шагов, и на занятой машине они не
 *  укладываются в одну фиксированную паузу. */
async function waitFor(ready: () => boolean, timeout = 5000) {
  const deadline = Date.now() + timeout;
  while (!ready()) {
    if (Date.now() > deadline) throw new Error("лента не дошла до ожидаемого состояния");
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  }
}

/** Уход на соседнюю вкладку и возврат обратно. */
async function returnToTab() {
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
    await new Promise(resolve => setTimeout(resolve, 20));
  });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}

it("возврат из соседней вкладки не перезагружает показанные картинки", async () => {
  await act(async () => root.render(<Transcript />));
  await waitFor(() => container.querySelectorAll("img").length === 2);

  const images = [...container.querySelectorAll("img")];
  expect(images).toHaveLength(2);
  expect(images.map(image => image.getAttribute("src"))).toEqual(["blob:preview-1", "blob:preview-2"]);
  expect(container.textContent).not.toContain("Проверяем вложение…");
  // Цена первого показа: метаданные и байты каждой картинки по одному разу.
  const firstShow = fileRequests();
  expect(firstShow.filter(url => url.includes("/api/files/attachment"))).toHaveLength(2);
  expect(firstShow.filter(url => url.includes("/api/files/download"))).toHaveLength(2);

  requests = [];
  for (let visit = 0; visit < 3; visit++) await returnToTab();

  // Три одинаковых цикла «ушёл — вернулся»: картинки не перезапрашиваются.
  expect(fileRequests()).toEqual([]);
  expect(revoked).toEqual([]);
  expect(container.textContent).not.toContain("Проверяем вложение…");
  // Те же самые узлы DOM — ни один пузырь не перемонтировался.
  expect([...container.querySelectorAll("img")]).toEqual(images);
});
