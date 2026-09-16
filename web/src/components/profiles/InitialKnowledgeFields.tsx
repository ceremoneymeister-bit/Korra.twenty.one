import { useState } from "react";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Textarea } from "@nous-research/ui/ui/components/textarea";
import type { KnowledgeDraft } from "@/lib/initial-knowledge";

export function InitialKnowledgeFields({ value, onChange, disabled, cloning }: {
  value: KnowledgeDraft;
  onChange: (draft: KnowledgeDraft) => void;
  disabled: boolean;
  cloning: boolean;
}) {
  const [open, setOpen] = useState(false);
  const change = <K extends keyof KnowledgeDraft>(key: K, next: KnowledgeDraft[K]) => onChange({ ...value, [key]: next });
  return <div className="grid gap-3 border-t pt-4">
    <button type="button" className="w-fit text-left font-medium" aria-expanded={open} aria-controls="pb-knowledge" onClick={() => setOpen(!open)}>
      {open ? "−" : "+"} Память и материалы <span className="text-sm font-normal text-[var(--neo-text-secondary)]">— необязательно</span>
    </button>
    <fieldset id="pb-knowledge" disabled={disabled} className={open ? "grid min-w-0 gap-4" : "hidden"}>
      <p className="text-sm text-[var(--neo-text-secondary)]">Короткие факты агент будет учитывать с первого сообщения. Большие документы добавьте в материал: агент сможет обращаться к нему по задаче.</p>
      <div className="grid gap-2">
        <Label htmlFor="pb-memory">Что агенту нужно знать о работе</Label>
        <Textarea id="pb-memory" value={value.memory} maxLength={50000} onChange={(e) => change("memory", e.target.value)} placeholder="Например: самовывоз с 11 до 18, доставка по средам и пятницам." />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="pb-user-memory">Что помнить о вас</Label>
        <Textarea id="pb-user-memory" value={value.user} maxLength={50000} onChange={(e) => change("user", e.target.value)} placeholder="Например: обращайтесь ко мне на вы; суммы показывайте в рублях." />
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer">Объём памяти</summary>
        <p className="my-2 text-[var(--neo-text-secondary)]">{cloning ? "Пустые поля сохранят лимиты исходного агента." : "Обычно достаточно 2 200 символов о работе и 1 375 о вас. Пустые поля сохранят настройки агента."} Увеличение объёма добавляет текст к каждому новому диалогу.</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-2"><Label htmlFor="pb-memory-limit">О работе, символов</Label><Input id="pb-memory-limit" type="number" min={100} max={50000} step={1} placeholder={cloning ? "Как у исходного агента" : "2200"} value={value.memoryLimit} onChange={(e) => change("memoryLimit", e.target.value)} /></div>
          <div className="grid gap-2"><Label htmlFor="pb-user-limit">О вас, символов</Label><Input id="pb-user-limit" type="number" min={100} max={50000} step={1} placeholder={cloning ? "Как у исходного агента" : "1375"} value={value.userLimit} onChange={(e) => change("userLimit", e.target.value)} /></div>
        </div>
      </details>
      <div className="grid gap-2 border-t pt-3">
        <Label htmlFor="pb-material-title">Первый материал</Label>
        <Input id="pb-material-title" value={value.title} maxLength={120} onChange={(e) => change("title", e.target.value)} placeholder="Название: например, условия доставки" />
        <Label htmlFor="pb-material-text">Текст материала</Label>
        <Textarea id="pb-material-text" value={value.text} maxLength={90000} onChange={(e) => change("text", e.target.value)} placeholder="Условия работы, инструкция или справочная информация" />
        <Label htmlFor="pb-material-url">Ссылка на источник</Label>
        <Input id="pb-material-url" type="url" value={value.url} maxLength={2048} onChange={(e) => change("url", e.target.value)} placeholder="https://…" />
        <Label htmlFor="pb-material-file">Или прикрепите документ</Label>
        <input id="pb-material-file" type="file" accept=".txt,.md,.csv,.json,.pdf,.docx,.xlsx" className="w-full min-w-0 text-sm" onChange={(e) => change("file", e.target.files?.[0] ?? null)} />
        <p className="text-xs text-[var(--neo-text-secondary)]">До 10 МБ. TXT, MD, CSV, JSON, PDF, DOCX или XLSX. Можно дополнить документ пояснением и ссылкой.</p>
      </div>
    </fieldset>
  </div>;
}
