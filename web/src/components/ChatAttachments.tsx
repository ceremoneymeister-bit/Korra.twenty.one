/**
 * Attachment chips (composer) and cards (transcript).
 *
 * Shape follows what people already know from Claude and ChatGPT: a bordered
 * tile with a type icon, the file name and a size/type caption; images show a
 * thumbnail instead of an icon. The owner never sees a filesystem path — that
 * is the model's business, not theirs.
 */

import {
  FileText,
  FileSpreadsheet,
  FileImage,
  FileAudio,
  FileArchive,
  Presentation,
  File as FileIcon,
  X,
  RotateCw,
  AlertCircle,
} from "lucide-react";
import type { ComponentType } from "react";

import { cn } from "@/lib/utils";
import {
  formatSize,
  isImageKind,
  shortName,
  type PendingAttachment,
} from "@/lib/chat-attachments";
import type { AttachmentDisplay } from "@/lib/chat-types";

const KIND_ICON: Record<string, ComponentType<{ size?: number; className?: string }>> = {
  pdf: FileText,
  doc: FileText,
  docx: FileText,
  rtf: FileText,
  txt: FileText,
  md: FileText,
  json: FileText,
  csv: FileSpreadsheet,
  xls: FileSpreadsheet,
  xlsx: FileSpreadsheet,
  ppt: Presentation,
  pptx: Presentation,
  zip: FileArchive,
  ogg: FileAudio,
  oga: FileAudio,
  m4a: FileAudio,
  mp3: FileAudio,
  wav: FileAudio,
  mp4: FileAudio,
  mov: FileAudio,
};

const KIND_TONE: Record<string, string> = {
  pdf: "text-rose-500",
  doc: "text-sky-500",
  docx: "text-sky-500",
  xls: "text-emerald-500",
  xlsx: "text-emerald-500",
  csv: "text-emerald-500",
  ppt: "text-orange-500",
  pptx: "text-orange-500",
  zip: "text-amber-500",
};

function IconFor({ kind }: { kind: string }) {
  const lower = kind.toLowerCase();
  const Icon = isImageKind(lower) ? FileImage : (KIND_ICON[lower] ?? FileIcon);
  return (
    <Icon
      size={18}
      strokeWidth={1.5}
      className={cn("shrink-0", KIND_TONE[lower] ?? "text-muted-foreground")}
      aria-hidden="true"
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Composer chip — one per file being attached                        */
/* ------------------------------------------------------------------ */

export function AttachmentChip({
  item,
  onRemove,
  onRetry,
}: {
  item: PendingAttachment;
  onRemove: () => void;
  onRetry: () => void;
}) {
  const failed = item.status === "error";
  return (
    <div
      role="listitem"
      className="korra-chat-attachment-chip normal-case tracking-normal"
      data-status={item.status}
      title={failed ? item.error ?? item.name : item.name}
    >
      {item.previewUrl ? (
        <img
          src={item.previewUrl}
          alt=""
          className="korra-chat-attachment-preview korra-chat-attachment-chip__preview shrink-0 object-cover"
        />
      ) : (
        <IconFor kind={item.kind} />
      )}

      <div className="flex min-w-0 items-baseline gap-1.5 whitespace-nowrap">
        <span className="max-w-[180px] truncate text-xs leading-none">
          {shortName(item.name)}
        </span>
        <span className="shrink-0 text-[11px] leading-none text-muted-foreground">
          {failed ? (
            <span className="inline-flex items-center gap-1 text-destructive">
              <AlertCircle size={11} aria-hidden="true" /> Не загрузился · {formatSize(item.size)}
            </span>
          ) : item.status === "uploading" ? (
            `Загрузка ${item.progress}% · ${formatSize(item.size)}`
          ) : (
            `${item.kind.toUpperCase()} · ${formatSize(item.size)}`
          )}
        </span>
      </div>

      {item.status === "uploading" && (
        <div
          className="korra-chat-attachment-chip__progress"
          role="progressbar"
          aria-label={`Загрузка ${item.name}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={item.progress}
        >
          <div
            className="korra-chat-attachment-chip__progress-value"
            style={{ width: `${item.progress}%` }}
          />
        </div>
      )}

      {failed && (
        <button
          type="button"
          onClick={onRetry}
          className="korra-chat-attachment-chip__action"
          aria-label={`Повторить загрузку ${item.name}`}
          title="Повторить"
        >
          <RotateCw size={14} aria-hidden="true" />
        </button>
      )}
      <button
        type="button"
        onClick={onRemove}
        className="korra-chat-attachment-chip__action"
        aria-label={`Убрать ${item.name}`}
        title="Убрать"
      >
        <X size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Transcript card — one per attachment inside a sent message         */
/* ------------------------------------------------------------------ */

export function AttachmentCard({ item }: { item: AttachmentDisplay }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border border-border/70 bg-background/60 px-2 py-1.5",
        "font-sans normal-case tracking-normal",
      )}
      title={item.name}
    >
      <IconFor kind={item.kind} />
      <div className="min-w-0">
        <div className="text-xs leading-tight truncate max-w-[220px]">
          {shortName(item.name, 34)}
        </div>
        <div className="text-[11px] leading-tight text-muted-foreground">
          {item.kind.toUpperCase()} · {item.sizeLabel}
        </div>
      </div>
    </div>
  );
}

export function AttachmentCardList({ items }: { items: AttachmentDisplay[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {items.map((item) => (
        <AttachmentCard key={item.key} item={item} />
      ))}
    </div>
  );
}
