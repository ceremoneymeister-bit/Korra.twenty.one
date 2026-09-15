import { useEffect, useId, useRef, useState } from "react";
import { DropdownMenu as Menu } from "radix-ui";
import { MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@nous-research/ui/ui/components/dialog";
import { Button } from "@/components/ProductButton";
import { api } from "@/lib/api";
import { ownerFacingError } from "@/lib/owner-facing-error";

interface SessionActionsProps {
  sessionId: string;
  title: string;
  profile: string;
  onRenamed?: () => void;
  onDelete: () => void;
}

/** A rename is bound to the row's profile and session, even during navigation. */
export function SessionActions({ sessionId, title, profile, onRenamed, onDelete }: SessionActionsProps) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inputId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const save = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      // The existing server contract sanitizes titles, checks uniqueness and
      // stores user provenance so a late auto-title cannot overwrite this one.
      await api.renameSession(sessionId, value, profile);
      if (!mounted.current) return;
      setEditing(false);
      onRenamed?.();
    } catch (cause) {
      if (!mounted.current) return;
      const message = cause instanceof Error ? cause.message : "";
      setError(/already in use/.test(message)
        ? "Чат с таким названием уже есть. Выберите другое название."
        : /Title too long/.test(message)
          ? "Название слишком длинное. Оставьте не больше 100 символов."
          : ownerFacingError(cause, "Не удалось переименовать чат. Ваш ввод сохранён — попробуйте ещё раз."));
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  const item = "flex min-h-[44px] cursor-pointer items-center gap-2 rounded-lg px-3 text-sm outline-none data-[highlighted]:shadow-[var(--neo-inset-compact)]";
  return <>
    <Menu.Root modal={false}>
      <Menu.Trigger asChild>
        <button ref={trigger} type="button" aria-label={`Действия с чатом «${title}»`}
          className="absolute right-0 top-1/2 flex size-[44px] -translate-y-1/2 items-center justify-center rounded-xl text-[var(--neo-text-secondary)] hover:shadow-[var(--neo-inset-compact)] focus-visible:ring-2 focus-visible:ring-primary">
          <MoreHorizontal size={18} aria-hidden />
        </button>
      </Menu.Trigger>
      <Menu.Portal><Menu.Content align="end" sideOffset={4} collisionPadding={12}
        className="z-50 min-w-44 rounded-xl bg-[var(--neo-surface)] p-1 text-[var(--neo-text-primary)] shadow-[var(--neo-depth-3)]"
        onCloseAutoFocus={event => { if (editing) event.preventDefault(); }}>
        <Menu.Item className={item} onSelect={() => { setValue(title); setError(""); setEditing(true); }}>
          <Pencil size={16} aria-hidden />Переименовать
        </Menu.Item>
        <Menu.Item className={`${item} text-destructive`} onSelect={onDelete}>
          <Trash2 size={16} aria-hidden />Удалить чат
        </Menu.Item>
      </Menu.Content></Menu.Portal>
    </Menu.Root>
    <Dialog open={editing} onOpenChange={next => { if (!busy) setEditing(next); }}>
      <DialogContent className="w-[calc(100vw-1.5rem)] max-w-md" aria-label="Переименовать чат"
        onCloseAutoFocus={event => { event.preventDefault(); trigger.current?.focus({ preventScroll: true }); }}>
        <DialogHeader className="pr-12">
          <DialogTitle>Переименовать чат</DialogTitle>
          <DialogDescription>До 100 символов. Пустое поле уберёт название.</DialogDescription>
        </DialogHeader>
        <form className="flex flex-col gap-3 px-5 pb-5 pt-4" onSubmit={event => { event.preventDefault(); void save(); }}>
          <label htmlFor={inputId} className="text-sm">Название чата</label>
          <input id={inputId} autoFocus value={value} disabled={busy} onChange={event => setValue(event.target.value)}
            className="min-h-[44px] w-full rounded-lg bg-[var(--neo-surface)] px-3 py-2 text-base shadow-[var(--neo-inset-compact)] outline-none focus-visible:ring-2 focus-visible:ring-primary"
            aria-invalid={Boolean(error)} aria-describedby={error ? `${inputId}-error` : undefined} />
          {error && <p id={`${inputId}-error`} role="alert" className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button outlined disabled={busy} onClick={() => setEditing(false)}>Отмена</Button>
            <Button type="submit" disabled={busy}>{busy ? "Сохраняем…" : "Сохранить"}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  </>;
}
