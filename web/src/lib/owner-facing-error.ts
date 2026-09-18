import { russianInterfaceText } from "./russian-interface-text";

type ErrorPayload = {
  detail?: unknown;
  message?: unknown;
  code?: unknown;
  type?: unknown;
  reason?: unknown;
  resets_at?: unknown;
  reset_at?: unknown;
  resets_in_seconds?: unknown;
  error?: ErrorPayload;
};

function resetDate(payload: ErrorPayload): Date | null {
  const nested = payload.error && typeof payload.error === "object" ? payload.error : undefined;
  const relative = nested?.resets_in_seconds ?? payload.resets_in_seconds;
  if (typeof relative === "number" && Number.isFinite(relative) && relative >= 0) {
    return new Date(Date.now() + relative * 1000);
  }
  const raw = nested?.resets_at ?? nested?.reset_at ?? payload.resets_at ?? payload.reset_at;
  if (typeof raw !== "string" && typeof raw !== "number") return null;
  const millis = typeof raw === "number"
    ? (raw > 10_000_000_000 ? raw : raw * 1000)
    : Date.parse(raw);
  const date = new Date(millis);
  return Number.isFinite(date.getTime()) ? date : null;
}

function resetText(payload: ErrorPayload): string | null {
  const date = resetDate(payload);
  if (!date) return null;
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

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
  let parsedPayload: ErrorPayload = {};
  try {
    const parsed: unknown = JSON.parse(payload);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new TypeError("not an error object");
    parsedPayload = parsed as ErrorPayload;
    const error = parsedPayload.error;
    code = [
      error?.code,
      error?.type,
      error?.reason,
      parsedPayload.code,
      parsedPayload.type,
      parsedPayload.reason,
    ].filter(value => typeof value === "string").join(" ");
    if (typeof parsedPayload.detail === "string") payload = parsedPayload.detail;
    else if (typeof error?.message === "string") payload = error.message;
    else if (typeof parsedPayload.message === "string") payload = parsedPayload.message;
  } catch { /* Older endpoints send plain text. */ }
  const signal = `${code} ${payload}`;
  const knownReset = resetText(parsedPayload);
  if (/insufficient_quota|usage_limit_reached|usage limit.*reached|exceeded your current quota|quota exceeded/i.test(signal)) {
    return knownReset
      ? `Лимит использования модели исчерпан. Он обновится ${knownReset} по времени этого устройства.`
      : "Лимит использования модели исчерпан. Проверьте в аккаунте модели, когда он обновится и действует ли подписка.";
  }
  if (/rate_limit|rate[- ]limited|too many requests|лимит.*запрос|временно ограничил запросы/i.test(signal)) {
    return knownReset
      ? `Модель временно не принимает запросы: достигнут лимит. Можно продолжить после ${knownReset} по времени этого устройства.`
      : "Модель временно не принимает запросы: достигнут лимит. Попробуйте позже. Точное время, когда можно продолжить, пока неизвестно.";
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
