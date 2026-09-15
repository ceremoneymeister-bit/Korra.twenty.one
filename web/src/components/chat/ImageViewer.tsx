/**
 * Просмотр изображения крупно поверх чата.
 *
 * До этого картинку в ленте можно было рассмотреть только скачиванием: человек
 * уходил в загрузки, открывал файл системным приложением и терял место в
 * переписке. Viewer показывает те же уже полученные байты во весь экран, даёт
 * исходный размер с прокруткой и возвращает фокус на карточку при закрытии.
 *
 * Один компонент обслуживает и вложение владельца, и результат агента
 * (`FileAttachment`, `ChatArtifact`): одна и та же картинка не должна вести
 * себя по-разному в разных карточках. Viewer только показывает — он не ходит
 * за файлом другим маршрутом, не меняет байты и ничего никуда не отправляет.
 * Маршрут, проверки доступа и «Скачать» остаются за карточкой.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { Download, Maximize2, Minimize2, X } from "lucide-react";

import { cn } from "@/lib/utils";

export interface ImageViewerProps {
  /** Ссылка, уже проверенная карточкой: object URL или маршрут Files. */
  src?: string;
  /** Имя файла — заголовок просмотра, alt и имя при скачивании. */
  name: string;
  /** Прямая ссылка «Скачать» — та же, что в исходной карточке. */
  downloadHref?: string;
  /** Скачивание авторизованным запросом, когда токен нельзя класть в ссылку. */
  onDownload?: () => void;
  downloading?: boolean;
  onClose: () => void;
}

/** Цель нажатия не меньше 44×44 CSS px и на узком экране. */
const CONTROL = cn(
  "inline-flex min-h-[44px] min-w-[44px] shrink-0 items-center justify-center gap-1.5",
  "rounded-[var(--neo-radius-control)] border-0 bg-[var(--neo-surface)] px-3 py-2",
  "text-xs text-[var(--neo-text-primary)] shadow-[var(--neo-depth-1)]",
  "outline-none focus-visible:shadow-[var(--neo-inset-compact)]",
);

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function ImageViewer({ src, name, downloadHref, onDownload, downloading, onClose }: ImageViewerProps) {
  const [zoom, setZoom] = useState(false);
  const [status, setStatus] = useState<"loading" | "ready" | "failed">(src ? "loading" : "failed");
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Закрытие часто приходит новой стрелочной функцией на каждый рендер. Держим
  // его в ref, иначе эффект перезапускался бы и терял исходный элемент фокуса.
  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; }, [onClose]);
  const drag = useRef<{ pointer: number; x: number; y: number; left: number; top: number } | null>(null);
  const dragged = useRef(false);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close.current();
        return;
      }
      if (event.key !== "Tab") return;
      const dialog = dialogRef.current;
      const nodes = dialog ? [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)] : [];
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = document.activeElement;
      const outside = !dialog?.contains(active);
      if (event.shiftKey ? active === first || outside : active === last || outside) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    };
    document.addEventListener("keydown", onKey);
    // Страница под просмотром не должна прокручиваться колесом; собственная
    // лента чата прокручивается своим контейнером и место чтения сохраняет.
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previous?.focus?.({ preventScroll: true });
    };
  }, []);

  useLayoutEffect(() => {
    const box = scrollRef.current;
    if (!box || !zoom) return;
    // Исходный размер открывается по центру картинки, а не от левого угла.
    box.scrollLeft = Math.max(0, (box.scrollWidth - box.clientWidth) / 2);
    box.scrollTop = Math.max(0, (box.scrollHeight - box.clientHeight) / 2);
  }, [zoom, status]);

  const toggleZoom = useCallback(() => setZoom(value => !value), []);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const box = scrollRef.current;
    if (!zoom || !box || event.button !== 0) return;
    dragged.current = false;
    drag.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, left: box.scrollLeft, top: box.scrollTop };
    box.setPointerCapture?.(event.pointerId);
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const box = scrollRef.current;
    const start = drag.current;
    if (!box || !start || start.pointer !== event.pointerId) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragged.current = true;
    box.scrollLeft = start.left - dx;
    box.scrollTop = start.top - dy;
  };

  const endDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const box = scrollRef.current;
    if (drag.current && box?.hasPointerCapture?.(event.pointerId)) box.releasePointerCapture(event.pointerId);
    drag.current = null;
  };

  return createPortal(
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label={`Просмотр изображения: ${name}`}
      /* Непрозрачный фон темы: просвечивающая лента под просмотром мешала
         читать и картинку, и подписи над ней. */
      className="fixed inset-0 z-[200] flex flex-col bg-[var(--neo-background)] font-sans normal-case tracking-normal"
      onClick={event => { if (event.target === event.currentTarget) close.current(); }}
    >
      <div className="flex flex-wrap items-center gap-2 p-3">
        <span className="min-w-0 flex-1 basis-full truncate text-sm text-[var(--neo-text-primary)] sm:basis-auto" title={name}>
          {name}
        </span>
        <button
          type="button"
          className={CONTROL}
          aria-pressed={zoom}
          aria-label={zoom ? "Показать по размеру окна" : "Показать в исходном размере — 100 %"}
          disabled={status !== "ready"}
          onClick={toggleZoom}
        >
          {zoom ? <Minimize2 size={14} aria-hidden /> : <Maximize2 size={14} aria-hidden />}
          {zoom ? "По размеру окна" : "100 %"}
        </button>
        {downloadHref && (
          <a
            href={downloadHref}
            download={name}
            className={CONTROL}
            aria-label={`Скачать ${name}`}
            aria-disabled={downloading}
            onClick={event => {
              if (!onDownload) return;
              event.preventDefault();
              onDownload();
            }}
          >
            <Download size={14} aria-hidden /> {downloading ? "Скачиваем…" : "Скачать"}
          </a>
        )}
        <button ref={closeRef} type="button" className={CONTROL} aria-label="Закрыть просмотр" onClick={() => close.current()}>
          <X size={16} aria-hidden /> Закрыть
        </button>
      </div>
      <div
        ref={scrollRef}
        data-zoom={zoom ? "full" : "fit"}
        className={cn(
          "relative min-h-0 flex-1 px-3 pb-3",
          zoom ? "overflow-auto" : "flex items-center justify-center overflow-hidden",
        )}
        onClick={event => { if (event.target === event.currentTarget && !dragged.current) close.current(); }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        {status === "failed" ? (
          <p role="alert" className="max-w-md text-center text-sm text-[var(--neo-text-secondary)]">
            Не удалось открыть изображение. Файл могли удалить, переместить или закрыть к нему доступ. Попробуйте скачать оригинал.
          </p>
        ) : (
          <>
            {status === "loading" && (
              <p role="status" className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-sm text-[var(--neo-text-secondary)]">
                Загружаем изображение…
              </p>
            )}
            <img
              src={src}
              alt={name}
              draggable={false}
              data-testid="image-viewer-image"
              onLoad={() => setStatus("ready")}
              onError={() => setStatus("failed")}
              onClick={event => {
                event.stopPropagation();
                if (!dragged.current && status === "ready") toggleZoom();
              }}
              className={cn(
                "select-none rounded-[var(--neo-radius-control)]",
                status === "loading" && "opacity-0",
                zoom
                  ? "max-h-none max-w-none cursor-grab active:cursor-grabbing"
                  : "max-h-full max-w-full cursor-zoom-in object-contain",
              )}
            />
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
