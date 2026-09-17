import { russianInterfaceText } from "./russian-interface-text";

/** Known machine signals before prose. A bare 429 never proves expired billing. */
export function ownerFacingError(
  exception: unknown,
  fallback = "Не удалось выполнить действие. Повторите попытку.",
): string {
  const raw = exception instanceof Error ? exception.message : String(exception ?? "");
  const statusMatch = /^(\d{3}):\s*([\s\S]*)$/.exec(raw.trim());
  const status = statusMatch ? Number(statusMatch[1]) : null;
  let payload = (statusMatch?.[2] ?? raw).trim();
  let code = "";
  try {
    const parsed = JSON.parse(payload) as { detail?: unknown; error?: { message?: unknown; code?: unknown; type?: unknown } };
    code = [parsed.error?.code, parsed.error?.type].filter(value => typeof value === "string").join(" ");
    if (typeof parsed.detail === "string") payload = parsed.detail;
    else if (typeof parsed.error?.message === "string") payload = parsed.error.message;
  } catch { /* Older endpoints send plain text. */ }
  const signal = `${code} ${payload}`;
  if (/insufficient_quota|usage_limit_reached|usage limit.*reached|exceeded your current quota|quota exceeded/i.test(signal)) {
    return "Лимит использования модели исчерпан. Проверьте в аккаунте модели, когда он обновится и действует ли подписка.";
  }
  if (/rate_limit|rate[- ]limited|too many requests|лимит.*запрос|временно ограничил запросы/i.test(signal)) {
    return "Модель временно не принимает запросы: достигнут лимит. Попробуйте позже. Точное время, когда можно продолжить, пока неизвестно.";
  }
  if (/token.*expired|invalid.*api.?key|authentication_error|invalid_api_key/i.test(signal)) {
    return "Нужно заново войти в аккаунт модели. Откройте настройки подключения и повторите вход.";
  }
  if (/context_length_exceeded|context window|maximum context length/i.test(signal)) {
    return "В этой беседе слишком много текста для одного запроса. Сократите запрос или начните новый чат, добавив нужные материалы и краткое описание задачи.";
  }
  if (/overloaded_error|overloaded|model.*unavailable/i.test(signal)) {
    return "Сервис модели сейчас перегружен или временно недоступен. Попробуйте позже; переподключать аккаунт из-за этого не нужно.";
  }
  if (/failed to fetch|networkerror|network request failed|load failed/i.test(payload)) {
    return "Не удалось связаться с сервером. Проверьте интернет. Если другие сайты открываются, попробуйте позже или обратитесь в поддержку.";
  }
  if (/abort(ed|error)?/i.test(payload)) return "Действие прервано. Можно повторить.";
  const safePayload = russianInterfaceText(payload);
  if (safePayload) return safePayload;
  if (/file already exists/i.test(payload)) return "Файл с таким именем уже есть. Переименуйте его и повторите загрузку.";
  if (status === 401) return "Вход в кабинет истёк. Войдите заново, чтобы продолжить.";
  if (status === 403) return "Для этого действия нет доступа. Проверьте подключение и разрешения в настройках.";
  if (status === 404) return "Не удалось найти эти данные. Обновите список и проверьте, не были ли они удалены.";
  if (status === 409) return "Данные уже изменились. Обновите экран и повторите.";
  if (status === 413) return "Файл слишком большой. Выберите файл меньшего размера.";
  if (status === 429) return "Слишком много запросов за короткое время. Подождите немного и повторите.";
  if (status === 504 || /timed? ?out|timeout/i.test(payload)) return "Не удалось дождаться ответа сервера. Проверьте состояние задачи перед повторной отправкой.";
  if (status !== null && status >= 500) return "Сервис временно недоступен. Попробуйте позже. Если ошибка повторяется, обратитесь в поддержку.";
  return fallback;
}
