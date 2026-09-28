import { useCallback, useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { X } from "lucide-react";

import { useModalBehavior } from "@/hooks/useModalBehavior";

import "./agents-mobile.css";

export interface AgentSheetProps {
  titleId: string;
  title: ReactNode;
  onClose: () => void;
  /** Кнопка в заголовке рядом с крестиком («Добавить»). */
  headExtra?: ReactNode;
  /** Между заголовком и прокруткой (поиск, карточка агента). */
  beforeBody?: ReactNode;
  footer?: ReactNode;
  /** Под подвалом, над ручкой (подсказка «Вид: вкладки · Изменить»). */
  afterBody?: ReactNode;
  children: ReactNode;
}

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Шторка экрана агентов: раскрывается вниз от шапки, шапка остаётся видна —
 * понятно, откуда открылся список и как вернуться.
 *
 * Лежит внутри области под шапкой, а не порталом поверх всего экрана.
 * Escape, затемнение и крестик закрывают; фокус переходит в шторку и
 * возвращается к кнопке, которая её открыла; Tab не уходит наружу.
 */
export function AgentSheet({
  titleId,
  title,
  onClose,
  headExtra,
  beforeBody,
  footer,
  afterBody,
  children,
}: AgentSheetProps) {
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => onClose(), [onClose]);
  useModalBehavior({ open: true, onClose: close });

  useEffect(() => {
    // Фокус на крестик, а не в поле поиска: на телефоне поле подняло бы
    // клавиатуру поверх списка, который открыли, чтобы просто ткнуть.
    closeRef.current?.focus({ preventScroll: true });
  }, []);

  const trapTab = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Tab" || !panelRef.current) return;
    const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
      (item) => item.offsetParent !== null || item === document.activeElement,
    );
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="k-sheet-layer" data-agent-sheet>
      <button type="button" className="k-scrim" aria-label="Закрыть" tabIndex={-1} onClick={close} />
      <section
        ref={panelRef}
        className="k-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={trapTab}
      >
        <div className="k-sheet__head">
          <h2 id={titleId} className="k-sheet__title">{title}</h2>
          {headExtra}
          <button ref={closeRef} type="button" className="k-ib" aria-label="Закрыть" onClick={close}>
            <X size={20} aria-hidden className="k-icon" />
          </button>
        </div>
        {beforeBody}
        <div className="k-sheet__body">{children}</div>
        {footer && <div className="k-sheet__foot">{footer}</div>}
        {afterBody}
        <div className="k-sheet__grabber" aria-hidden />
      </section>
    </div>
  );
}
