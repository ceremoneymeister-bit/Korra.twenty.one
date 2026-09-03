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
      className={cn(
        "group relative flex min-h-11 items-center gap-2 rounded-lg border py-1 ps-2 pe-1",
        "bg-background/60 font-sans normal-case tracking-normal",
        failed ? "border-destructive/60" : "border-input",
      )}
      title={failed ? item.error ?? item.name : item.name}
    >
      {item.previewUrl ? (
        <img
          src={item.previewUrl}
          alt=""
          className="korra-chat-attachment-preview size-8 shrink-0 rounded-md object-cover"
        />
      ) : (
        <IconFor kind={item.kind} />
      )}

      <div className="min-w-0">
        <div className="text-xs leading-tight truncate max-w-[180px]">
          {shortName(item.name)}
        </div>
        <div className="text-[11px] leading-tight text-muted-foreground">
          {failed ? (
            <span className="text-destructive inline-flex items-center gap-1">
              <AlertCircle size={11} aria-hidden="true" /> Не загрузился · {formatSize(item.size)}
            </span>
          ) : item.status === "uploading" ? (
            `Загрузка ${item.progress}% · ${formatSize(item.size)}`
          ) : (
            `${item.kind.toUpperCase()} · ${formatSize(item.size)}`
          )}
        </div>
      </div>

      {item.status === "uploading" && (
        <div
          className="absolute inset-x-0 bottom-0 h-0.5 bg-muted/40 rounded-b-md overflow-hidden"
          role="progressbar"
          aria-label={`Загрузка ${item.name}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={item.progress}
        >
          <div
            className="h-full bg-primary motion-safe:transition-[width] motion-safe:duration-[var(--duration-quick)]"
            style={{ width: `${item.progress}%` }}
          />
        </div>
      )}

      {failed && (
        <button
          type="button"
          onClick={onRetry}
          className="flex size-10 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          aria-label={`Повторить загрузку ${item.name}`}
          title="Повторить"
        >
          <RotateCw size={14} aria-hidden="true" />
        </button>
      )}
      <button
        type="button"
        onClick={onRemove}
        className="flex size-10 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
