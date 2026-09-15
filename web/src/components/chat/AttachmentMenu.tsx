import { DropdownMenu as Menu } from "radix-ui";
import { Check, ImageIcon, Paperclip, Plus } from "lucide-react";

/**
 * Компактное меню вложений на кнопке «+»: прикрепить файл с устройства и
 * выбор качества фото. Переключатель «Отправлять оригиналы» действует для
 * новых загрузок и выбирается ДО загрузки; уже подготовленное вложение он
 * не пересобирает — об этом говорит подпись. Раньше это был голый
 * <input type="checkbox"> под полем ввода, вне общего стиля контролов.
 */
export function AttachmentMenu({ disabled, onPickFiles, originals, onOriginalsChange }: {
  disabled: boolean;
  onPickFiles: () => void;
  originals: boolean;
  onOriginalsChange: (value: boolean) => void;
}) {
  const item = "flex min-h-11 w-full cursor-pointer select-none items-center gap-3 rounded-xl px-3 py-2 text-left text-sm text-[var(--neo-text-primary)] outline-0 data-[highlighted]:shadow-[var(--neo-inset-compact)] data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50";
  return (
    <Menu.Root modal={false}>
      <Menu.Trigger asChild>
        <button
          type="button"
          disabled={disabled}
          className="korra-chat-composer__control korra-chat-composer__attach"
          aria-label="Прикрепить файл"
          title="Вложения и качество фото"
        >
          <Plus size={20} strokeWidth={1.5} aria-hidden />
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={12}
          aria-label="Вложения"
          className="z-50 w-[min(20rem,calc(100vw-1.5rem))] rounded-2xl bg-[var(--neo-surface)] p-2 text-[var(--neo-text-primary)] shadow-[var(--neo-depth-3)] outline-0"
        >
          <Menu.Item className={item} onSelect={onPickFiles}>
            <Paperclip size={16} aria-hidden className="shrink-0 text-[var(--neo-text-secondary)]" />
            <span className="min-w-0 flex-1">
              <span className="block">Прикрепить файл с устройства</span>
              <span className="block text-xs text-[var(--neo-text-secondary)]">Файлы, фото, документы — до 30 вложений</span>
            </span>
          </Menu.Item>
          <Menu.Separator className="my-1 h-px bg-[var(--neo-shadow)] opacity-40" />
          <Menu.CheckboxItem
            className={item}
            checked={originals}
            onCheckedChange={onOriginalsChange}
            // Оставляем меню открытым: переключатель должен показать новое
            // состояние, а не исчезнуть после нажатия.
            onSelect={event => event.preventDefault()}
          >
            <span aria-hidden className="flex size-5 shrink-0 items-center justify-center rounded-md bg-[var(--neo-surface)] shadow-[var(--neo-inset-compact)]">
              <Menu.ItemIndicator><Check size={14} /></Menu.ItemIndicator>
            </span>
            <ImageIcon size={16} aria-hidden className="shrink-0 text-[var(--neo-text-secondary)]" />
            <span className="min-w-0 flex-1">
              <span className="block">Отправлять оригиналы фото</span>
              <span className="block text-xs text-[var(--neo-text-secondary)]">
                {originals ? "Без сжатия. Действует для новых загрузок." : "Сейчас крупные фото сжимаются. Выбор — до загрузки."}
              </span>
            </span>
          </Menu.CheckboxItem>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
