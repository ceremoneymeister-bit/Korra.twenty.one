import type { api } from "@/lib/api";

export interface KnowledgeDraft {
  memory: string;
  user: string;
  memoryLimit: string;
  userLimit: string;
  title: string;
  text: string;
  url: string;
  file: File | null;
}
export const emptyKnowledge = (): KnowledgeDraft => ({
  memory: "", user: "", memoryLimit: "", userLimit: "",
  title: "", text: "", url: "", file: null,
});

type Knowledge = NonNullable<Parameters<typeof api.createProfile>[0]["initial_knowledge"]>;
const MAX_FILE = 10 * 1024 * 1024;

export async function prepareInitialKnowledge(draft: KnowledgeDraft): Promise<Knowledge | undefined> {
  if (!Object.values(draft).some((value) => value instanceof File || (typeof value === "string" && value.trim()))) return;
  const limit = (value: string) => {
    if (!value.trim()) return undefined;
    const n = Number(value);
    if (!Number.isInteger(n) || n < 100 || n > 50000) throw new Error("Лимит памяти — целое число от 100 до 50 000 символов.");
    return n;
  };
  const result: Knowledge = {
    memory: draft.memory.trim() ? [draft.memory.trim()] : [],
    user: draft.user.trim() ? [draft.user.trim()] : [],
    memory_char_limit: limit(draft.memoryLimit),
    user_char_limit: limit(draft.userLimit),
  };
  if (draft.file || draft.text.trim() || draft.url.trim() || draft.title.trim()) {
    const title = draft.title.trim() || draft.file?.name || "";
    if (!title) throw new Error("Добавьте название материала.");
    if (!draft.file && !draft.text.trim() && !draft.url.trim()) throw new Error("Добавьте текст, ссылку или файл материала.");
    result.material = { title, text: draft.text.trim(), url: draft.url.trim() };
    if (draft.file) {
      if (!draft.file.size || draft.file.size > MAX_FILE) throw new Error("Выберите непустой файл размером до 10 МБ.");
      if (!/\.(txt|md|csv|json|pdf|docx|xlsx)$/i.test(draft.file.name)) throw new Error("Поддерживаются TXT, MD, CSV, JSON, PDF, DOCX и XLSX.");
      const file = draft.file;
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
        reader.onerror = () => reject(new Error("Не удалось прочитать файл. Выберите его заново."));
        reader.readAsDataURL(file);
      });
      result.material.filename = file.name;
      result.material.data_base64 = encoded;
    }
  }
  return result;
}

