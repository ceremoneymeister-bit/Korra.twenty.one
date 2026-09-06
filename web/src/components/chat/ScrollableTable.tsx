import { useEffect, useId, useRef, useState, type ReactNode } from "react";

/** Подсказываем прокрутку только тогда, когда столбцы действительно не помещаются. */
export function ScrollableTable({ children }: { children: ReactNode }) {
  const viewport = useRef<HTMLDivElement>(null);
  const hintId = useId();
  const [overflow, setOverflow] = useState(false);
  useEffect(() => {
    const el = viewport.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setOverflow(el.scrollWidth > el.clientWidth + 1));
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="korra-markdown__table-block">
      {overflow && <p id={hintId} className="korra-markdown__table-hint">Таблицу можно прокручивать вбок</p>}
      <div ref={viewport} className="korra-markdown__table" role="region" tabIndex={0} aria-label="Таблица" aria-describedby={overflow ? hintId : undefined}>
        {children}
      </div>
    </div>
  );
}
