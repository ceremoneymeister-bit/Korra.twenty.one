import { useEffect, useLayoutEffect, useRef } from "react";

/**
 * Hook that adds standard modal behaviors when `open` is true:
 * - Escape key calls `onClose`
 * - Body scroll is locked
 * - Focus is restored to the previously focused element on close
 *
 * Returns a ref to attach to the modal container (for optional future focus trapping).
 */
export function useModalBehavior({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  // The latest callback, read at Escape time. A parent that re-renders with a
  // new closure (a chat list refreshing every few seconds) must not tear the
  // modal down and throw focus out of the field the person is typing in.
  const onCloseRef = useRef(onClose);
  useLayoutEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return;

    const prevActive = document.activeElement as HTMLElement | null;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
      }
    };

    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      prevActive?.focus?.();
    };
  }, [open]);

  return containerRef;
}
