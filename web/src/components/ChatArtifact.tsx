/**
 * Показ артефакта в ленте: картинка — картинкой, документ — карточкой.
 *
 * До этого агент отдавал путь строкой, и владельцу приходилось идти в раздел
 * «Материалы», искать файл и открывать его там. Для продукта, который меряет
 * время владельца, это прямой расход лимита на навигацию.
 */

import { useState } from "react";
import { Download, ExternalLink } from "lucide-react";

import { cn } from "@/lib/utils";
import { artifactUrl, shortName, type ChatArtifact } from "@/lib/chat-artifacts";
import { AttachmentCard } from "@/components/ChatAttachments";
import { ApprovalCard, ApprovalSettled } from "@/components/chat/ApprovalCard";
import { FileAttachment } from "@/components/chat/FileAttachment";

export type ArtifactDecision = "approve" | "change" | "defer";
/** Возвращает false, если решение отправить не удалось — карточка тогда не
 *  показывает «отправлено». */
export type ArtifactDecisionHandler = (
  kind: ArtifactDecision,
  item: ChatArtifact,
) => void | boolean | Promise<void | boolean>;

/**
 * Вопрос владельцу под артефактом.
 *
 * Это не украшение: именно он заменяет владельцу поход в таблицу. Решение
 * уходит обычным сообщением в чат, агент подхватывает его штатным правилом
 * «подтверждение → обновить статус → вернуть сводку».
 *
 * Форма — карточка по эталону владельца (03.09.2026), протокол прежний:
 * `onDecision("approve" | "change" | "defer", item)`, «Изменить» по-прежнему
 * не отправляет ничего, а подставляет черновик в поле ввода.
 */
function DecisionRow({
  item,
  onDecision,
  busy,
  already,
}: {
  item: ChatArtifact;
  onDecision: ArtifactDecisionHandler;
  /** Идёт ответ агента: отправить решение сейчас нельзя. */
  busy?: boolean;
  /** Решение, уже принятое в этом разговоре — восстановлено из истории. */
  already?: ArtifactDecision;
}) {
  const [sent, setSent] = useState<ArtifactDecision | null>(null);
  const [failed, setFailed] = useState(false);
  // «Пропустить» ничего не отправляет и ничего не теряет: карточка сжимается
  // до кнопки, решение остаётся доступным. Скрывать вопрос совсем нельзя —
  // артефакт так и остался бы нерешённым, и владелец узнал бы об этом только
  // от агента.
  const [skipped, setSkipped] = useState(false);
  // Своё состояние живёт до перезагрузки, история — всегда. Показываем то, что
  // знает история, если она знает.
  const done = already ?? sent;

  if (done === "approve" || done === "defer") {
    return <ApprovalSettled decision={done} />;
  }

  if (skipped) {
    return (
      <button
        type="button"
        onClick={() => setSkipped(false)}
        className={cn(
          "mt-1.5 rounded-[var(--neo-radius-round)] px-2 py-1",
          "border-0 bg-transparent outline-0 cursor-pointer",
          "font-sans text-[11px] normal-case tracking-normal",
          "text-[var(--neo-text-secondary)]",
          "hover:shadow-[var(--neo-inset-compact)]",
        )}
      >
        Решить по «{shortName(item.name, 28)}»
      </button>
    );
  }

  return (
    <ApprovalCard
      question={`Что делаем с «${shortName(item.name, 44)}»?`}
      busy={busy}
      failed={failed}
      onSkip={() => setSkipped(true)}
      onSubmit={async (choice) => {
        if (choice === "change") {
          // «Изменить» — не решение, а начало разговора: наверх уходит тот же
          // вызов, и хозяин чата подставляет черновик в поле.
          void onDecision("change", item);
          return;
        }
        // Помечаем только по факту доставки: send() возвращает false и при
        // оборванной сети, и во время чужого потока. Карточка не должна
        // рапортовать об успехе, которого не было.
        setFailed(false);
        const ok = await onDecision(choice, item);
        if (ok === false) setFailed(true);
        else setSent(choice);
      }}
    />
  );
}

export function ChatArtifactView({
  item,
  onDecision,
  busy,
  decided,
}: {
  item: ChatArtifact;
  onDecision?: ArtifactDecisionHandler;
  busy?: boolean;
  decided?: Map<string, ArtifactDecision>;
}) {
  if (item.isImage) {
    return <div>
      <FileAttachment path={item.path} name={item.name} />
      {onDecision && <DecisionRow item={item} onDecision={onDecision} busy={busy}
        already={decided?.get(item.path) ?? decided?.get(item.name)} />}
    </div>;
  }

  return (
    <div className="mt-2 mb-1">
      <div className="flex items-center gap-2">
      <AttachmentCard
        item={{
          key: item.path,
          name: item.name,
          kind: item.kind,
          sizeLabel: "артефакт",
        }}
      />
      <a
        href={artifactUrl(item.path)}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted/40 font-sans normal-case tracking-normal"
        title="Открыть в новой вкладке"
      >
        <ExternalLink size={12} aria-hidden /> открыть
      </a>
      <a
        href={artifactUrl(item.path, false)}
        download
        className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted/40 font-sans normal-case tracking-normal"
        title="Скачать"
      >
        <Download size={12} aria-hidden /> скачать
      </a>
      </div>
      {onDecision && (
        <DecisionRow
          item={item}
          onDecision={onDecision}
          busy={busy}
          already={decided?.get(item.path) ?? decided?.get(item.name)}
        />
      )}
    </div>
  );
}

export function ChatArtifactList({
  items,
  onDecision,
  busy,
  decided,
}: {
  items: ChatArtifact[];
  onDecision?: ArtifactDecisionHandler;
  busy?: boolean;
  decided?: Map<string, ArtifactDecision>;
}) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-col">
      {items.map((item) => (
        <ChatArtifactView
          key={item.path}
          item={item}
          onDecision={onDecision}
          busy={busy}
          decided={decided}
        />
      ))}
    </div>
  );
}
