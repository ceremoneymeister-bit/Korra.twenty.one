// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ImageViewer } from "./ImageViewer";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});

const closed = vi.fn();

/** Просмотр всегда открывается из карточки: фокус должен вернуться именно на неё. */
function Harness({ src = "blob:preview", downloadHref, onDownload }: {
  src?: string;
  downloadHref?: string;
  onDownload?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" aria-label="Открыть изображение крупно: кот.png" onClick={() => setOpen(true)}>
        превью
      </button>
      {open && (
        <ImageViewer
          src={src}
          name="кот.png"
          downloadHref={downloadHref}
          onDownload={onDownload}
          onClose={() => { setOpen(false); closed(); }}
        />
      )}
    </>
  );
}

function trigger(): HTMLButtonElement {
  return host.querySelector("button")!;
}

function dialog(): HTMLElement | null {
  return document.body.querySelector('[role="dialog"]');
}

function control(label: string): HTMLElement {
  return dialog()!.querySelector<HTMLElement>(`[aria-label="${label}"]`)!;
}

async function open() {
  await act(async () => root.render(<Harness />));
  // Браузер отдаёт фокус нажатой карточке; jsdom этого сам не делает.
  trigger().focus();
  await act(async () => { trigger().click(); });
}

async function loaded() {
  const image = dialog()!.querySelector("img")!;
  await act(async () => { image.dispatchEvent(new Event("load")); });
  return image;
}

it("нажатие на карточку открывает диалог просмотра с фокусом внутри", async () => {
  await open();
  const view = dialog();
  expect(view).not.toBeNull();
  expect(view!.getAttribute("aria-label")).toContain("кот.png");
  expect(view!.querySelector("img")!.getAttribute("src")).toBe("blob:preview");
  // Фокус обязан оказаться внутри диалога, иначе Tab уводит в ленту под ним.
  expect(view!.contains(document.activeElement)).toBe(true);
  expect(document.activeElement!.getAttribute("aria-label")).toBe("Закрыть просмотр");
});

it("Escape закрывает просмотр и возвращает фокус на исходный элемент", async () => {
  closed.mockClear();
  await open();
  await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
  expect(closed).toHaveBeenCalledOnce();
  expect(dialog()).toBeNull();
  expect(document.activeElement).toBe(trigger());
});

it("кнопка «Закрыть» закрывает просмотр и возвращает фокус", async () => {
  closed.mockClear();
  await open();
  await act(async () => { control("Закрыть просмотр").click(); });
  expect(closed).toHaveBeenCalledOnce();
  expect(dialog()).toBeNull();
  expect(document.activeElement).toBe(trigger());
});

it("Tab не уводит фокус из просмотра", async () => {
  await open();
  const focusable = [...dialog()!.querySelectorAll<HTMLElement>("a[href], button:not([disabled])")];
  focusable[focusable.length - 1].focus();
  await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true })); });
  expect(document.activeElement).toBe(focusable[0]);
});

it("переключает исходный размер и возвращает вид по размеру окна", async () => {
  await open();
  await loaded();
  const zoom = () => control("Показать в исходном размере — 100 %");
  const area = () => dialog()!.querySelector<HTMLElement>("[data-zoom]")!;
  expect(area().dataset.zoom).toBe("fit");
  expect(zoom().getAttribute("aria-pressed")).toBe("false");
  await act(async () => { zoom().click(); });
  expect(area().dataset.zoom).toBe("full");
  const back = control("Показать по размеру окна");
  expect(back.getAttribute("aria-pressed")).toBe("true");
  await act(async () => { back.click(); });
  expect(area().dataset.zoom).toBe("fit");
});

it("клик по самой картинке тоже переключает масштаб и не закрывает просмотр", async () => {
  closed.mockClear();
  await open();
  const image = await loaded();
  await act(async () => { image.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
  expect(dialog()!.querySelector<HTMLElement>("[data-zoom]")!.dataset.zoom).toBe("full");
  expect(closed).not.toHaveBeenCalled();
});

it("не открывшееся изображение объясняет причину и оставляет скачивание", async () => {
  await act(async () => root.render(<Harness downloadHref="/api/files/download?path=%2Fx&chat=1" />));
  await act(async () => { trigger().click(); });
  await act(async () => { dialog()!.querySelector("img")!.dispatchEvent(new Event("error")); });
  const alert = dialog()!.querySelector('[role="alert"]')!;
  expect(alert.textContent).toContain("Не удалось открыть изображение");
  expect(dialog()!.querySelector("img")).toBeNull();
  expect(control("Скачать кот.png").getAttribute("href")).toContain("/api/files/download?");
});

it("до загрузки показывает состояние ожидания, а увеличение недоступно", async () => {
  await open();
  expect(dialog()!.querySelector('[role="status"]')!.textContent).toContain("Загружаем изображение…");
  expect(control("Показать в исходном размере — 100 %").hasAttribute("disabled")).toBe(true);
  await loaded();
  expect(dialog()!.querySelector('[role="status"]')).toBeNull();
  expect(control("Показать в исходном размере — 100 %").hasAttribute("disabled")).toBe(false);
});

it("«Скачать» в просмотре идёт через обработчик карточки, а не переходом по ссылке", async () => {
  const download = vi.fn();
  await act(async () => root.render(<Harness downloadHref="/api/files/download?path=%2Fx&chat=1" onDownload={download} />));
  await act(async () => { trigger().click(); });
  const event = new MouseEvent("click", { bubbles: true, cancelable: true });
  await act(async () => { control("Скачать кот.png").dispatchEvent(event); });
  expect(download).toHaveBeenCalledOnce();
  expect(event.defaultPrevented).toBe(true);
  expect(dialog()).not.toBeNull();
});
