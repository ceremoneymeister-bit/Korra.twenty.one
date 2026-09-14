import { type SetStateAction } from "react";
import { MAX_ATTACHMENTS, type PendingAttachment, type UploadedAttachment, kindOf } from "@/lib/chat-attachments";
import { prepareUploadBatch } from "@/lib/upload-batch";
import { type FolderSelection, validateSelection } from "@/lib/folder-select";
import { $uploadJobs, startUploadJob, excludeUploadFile } from "@/store/upload-jobs";

/** A subscription owns callbacks for this exact draft, even after navigation. */
export async function attachUploadBatch(files: File[], options: {
  profile?: string;
  folder?: FolderSelection;
  originals?: boolean;
  set: (next: SetStateAction<PendingAttachment[]>) => void;
}): Promise<void> {
  let existing: PendingAttachment[] = [];
  options.set(list => { existing = list; return list; });
  if (!options.folder) {
    const seen = new Set(existing.map(item => `${item.file.name}:${item.file.size}:${item.file.lastModified}`));
    files = files.filter(file => {
      const key = `${file.name}:${file.size}:${file.lastModified}`;
      if (seen.has(key)) return false;
      seen.add(key); return true;
    });
    if (!files.length) return;
    const invalid = files.find(file => file.size === 0 || file.size > 2 * 1024 ** 3);
    if (invalid) throw new Error(`«${invalid.name}» — ${invalid.size ? "файл больше 2 ГБ" : "пустой файл"}.`);
  }
  const selection = validateSelection(options.folder ?? { name: "Файлы", files: files.map(file => ({ path: file.name, file })) });
  if (selection.files.length > 500) throw new Error("В чат можно загрузить до 500 файлов за раз.");
  const room = MAX_ATTACHMENTS - existing.length;
  if (room < 1) throw new Error("В сообщении уже 30 вложений. Отправьте их или удалите ненужные.");
  const grouped = Boolean(options.folder || selection.files.length > room);
  const placeholders: PendingAttachment[] = grouped ? [{
    id: crypto.randomUUID(), name: selection.name, kind: "folder", size: selection.files.reduce((sum, item) => sum + item.file.size, 0),
    file: new File([], selection.name), status: "preparing", progress: 0,
  }] : selection.files.map(({ file }) => ({
    id: crypto.randomUUID(), name: file.name, size: file.size, kind: kindOf(file.name), file,
    status: "preparing", progress: 0,
  }));
  const ids = new Set(placeholders.map(item => item.id));
  options.set(list => [...list, ...placeholders]);
  try {
    const input = await prepareUploadBatch(selection.files.map(item => item.file), {
      origin: "chat", profile: options.profile, folder: options.folder, grouped,
      originals: options.originals,
      onProgress: (done, total) => options.set(list => list.map(item => ids.has(item.id) ? { ...item, progress: total ? Math.round(100 * done / total) : 100 } : item)),
    });
    let remaining: PendingAttachment[] = [];
    options.set(list => {
      remaining = list.filter(item => ids.has(item.id));
      return list.map(item => ids.has(item.id) ? { ...item, uploadId: input.manifest.upload_id,
        uploadIndex: grouped ? undefined : placeholders.findIndex(candidate => candidate.id === item.id), status: "uploading", progress: 0 } : item);
    });
    if (!remaining.length) return;
    const id = startUploadJob(input);
    if (!grouped) placeholders.forEach((item, index) => {
      if (!remaining.some(candidate => candidate.id === item.id)) excludeUploadFile(id, index);
    });
    const update = () => {
      const job = $uploadJobs.get()[id];
      if (!job) { options.set(list => list.filter(item => !ids.has(item.id))); stop(); return; }
      options.set(list => list.flatMap(item => {
        if (!ids.has(item.id)) return [item];
        if (job.result) {
          const folder = job.result.folder;
          const uploaded: UploadedAttachment | undefined = folder
            ? { ...folder, kind: "folder", size: folder.total_bytes, reader: "directory" }
            : job.result.files.find(file => file.index === item.uploadIndex);
          return uploaded ? [{ ...item, ...uploaded, uploaded, status: "ready" as const, progress: 100,
            originalSize: item.size !== uploaded.size ? item.size : undefined }] : [];
        }
        return [{ ...item, status: job.status === "failed" ? "error" as const
          : job.status === "paused" || job.status === "pausing" ? "paused" as const : "uploading" as const,
          error: job.error, progress: job.progress.totalBytes ? Math.round(100 * job.progress.bytes / job.progress.totalBytes) : 0 }];
      }));
      if (job.result) stop();
    };
    const stop = $uploadJobs.listen(update);
    update();
  } catch (cause) {
    options.set(list => list.filter(item => !ids.has(item.id)));
    throw cause;
  }
}
