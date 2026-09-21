import { Button } from "@/components/ProductButton";
import { AlertTriangle } from "lucide-react";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "@/i18n";

interface ConfirmDialogProps {
  cancelLabel?: string;
  confirmLabel?: string;
  description?: string;
  destructive?: boolean;
  loading?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  open: boolean;
  title: string;
}

export function ConfirmDialog({
  cancelLabel,
  confirmLabel,
  description,
  destructive = false,
  loading = false,
  onCancel,
  onConfirm,
  open,
  title,
}: ConfirmDialogProps) {
  const { tr } = useI18n();
  const dialogRef = useRef<HTMLDivElement>(null);
  const resolvedCancelLabel = cancelLabel ?? tr("Cancel");
  const resolvedConfirmLabel = confirmLabel ?? tr("Confirm");

  useEffect(() => {
    if (!open) return;

    const prevActive = document.activeElement as HTMLElement | null;
    dialogRef.current
      ?.querySelector<HTMLButtonElement>("[data-cancel]")
      ?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
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
  }, [open, onCancel]);

  if (!open) return null;

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      aria-describedby={description ? "confirm-dialog-desc" : undefined}
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
      className="neo-overlay fixed inset-0 z-[200] flex items-center justify-center p-4"
    >
      <div
        ref={dialogRef}
        className="neo-dialog relative w-full max-w-md overflow-hidden font-sans"
      >
        <div className="flex items-start gap-4 px-5 pb-4 pt-5 sm:px-6 sm:pt-6">
          {destructive && (
            <div
              aria-hidden
              className="flex size-11 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive shadow-[var(--neo-inset-compact)]"
            >
              <AlertTriangle className="h-5 w-5" />
            </div>
          )}

          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <h2
              id="confirm-dialog-title"
              className="text-xl font-semibold leading-tight text-foreground"
            >
              {title}
            </h2>

            {description && (
              <p
                id="confirm-dialog-desc"
                className="whitespace-pre-line text-[15px] leading-relaxed text-text-secondary"
              >
                {description}
              </p>
            )}
          </div>
        </div>

        <div className="flex flex-col-reverse gap-2 px-5 pb-5 sm:flex-row sm:justify-end sm:px-6 sm:pb-6">
          <Button
            data-cancel
            type="button"
            outlined
            onClick={onCancel}
            disabled={loading}
            className="w-full sm:w-auto"
          >
            {resolvedCancelLabel}
          </Button>
          <Button
            data-confirm
            type="button"
            destructive={destructive}
            onClick={onConfirm}
            disabled={loading}
            className="w-full sm:w-auto"
          >
            {loading ? "…" : resolvedConfirmLabel}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
