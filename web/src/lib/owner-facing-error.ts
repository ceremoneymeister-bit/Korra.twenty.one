/** Turn transport/backend failures into concise Russian copy for product pages. */
export function ownerFacingError(
  exception: unknown,
  fallback = "Не удалось выполнить действие. Повторите попытку.",
): string {
  const raw = exception instanceof Error ? exception.message : String(exception ?? "");
  if (/failed to fetch|networkerror|network request failed|load failed/i.test(raw)) {
    return "Не удалось связаться с сервером. Проверьте интернет и повторите.";
  }
  if (/abort(ed|error)?/i.test(raw)) {
    return "Действие прервано. Можно повторить.";
  }

  const statusMatch = /^(\d{3}):\s*([\s\S]*)$/.exec(raw.trim());
  const status = statusMatch ? Number(statusMatch[1]) : null;
  const payload = (statusMatch?.[2] ?? raw).trim();
  try {
    const parsed = JSON.parse(payload) as { detail?: unknown };
    if (typeof parsed.detail === "string" && /[А-Яа-яЁё]/.test(parsed.detail)) {
      return parsed.detail.trim();
    }
  } catch {
    // Plain text is handled below.
  }

  if (/[А-Яа-яЁё]/.test(payload)) return payload;
  if (/file already exists/i.test(payload)) {
    return "Файл с таким именем уже есть. Переименуйте его и повторите загрузку.";
  }
  if (status === 403) return "Для этого действия нет доступа.";
  if (status === 404) return "Данные пока недоступны. Обновите экран чуть позже.";
  if (status === 409) return "Данные уже изменились. Обновите экран и повторите.";
  if (status === 413) return "Файл слишком большой для загрузки.";
  if (status !== null && status >= 500) {
    return "Сервис временно недоступен. Повторите через минуту.";
  }
  return fallback;
}
