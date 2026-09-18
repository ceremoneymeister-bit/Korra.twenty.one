/**
 * Решения человека в веб-чате.
 *
 * Старое одобрение команды блокирует ход агента до ответа. Решение по внешнему
 * эффекту, напротив, долговечно и не держит модельный ход: точный черновик
 * остаётся доступен после F5/рестарта и из любого чата профиля.
 * Поэтому у данных несколько путей в браузер и один обратно:
 *
 *   • живое событие `hermes.approval.request` в потоке `/api/chat/completions`
 *     (разбирается в `sse-parser`) — пока вкладка открыта;
 *   • `GET /api/chat/approvals?session_id=…` — состояние одного чата;
 *   • `GET /api/chat/decisions` — центр внешних эффектов всего профиля;
 *   • `POST /api/chat/approval` — сам ответ.
 *
 * Эти маршруты панель проксирует в движок (`/api/sessions/{id}/approval(s)`
 * в `gateway/platforms/api_server.py`), решение исполняет то же ядро
 * одобрений, что и на мессенджерах.
 */

import { authedFetch } from "@/lib/api";
import type {
  ApprovalChoiceValue,
  EffectDecisionStatus,
  SSEApprovalRequestData,
} from "@/lib/chat-types";

/** Порядок вариантов в карточке. Сервер присылает подмножество этого набора —
 *  например, без `always`, когда разрешать навсегда нельзя. */
export const APPROVAL_CHOICE_ORDER: ApprovalChoiceValue[] = [
  "once",
  "session",
  "always",
  "deny",
];

const EFFECT_STATUSES: EffectDecisionStatus[] = [
  "pending",
  "approved",
  "denied",
  "executing",
  "succeeded",
  "failed",
  "unknown",
];

function withProfile(url: string, profile?: string): string {
  const name = profile?.trim();
  if (!name) return url;
  return `${url}${url.includes("?") ? "&" : "?"}profile=${encodeURIComponent(name)}`;
}

/** Оставить только то, что действительно можно отправить: без `request_id`
 *  решение некуда адресовать, а неизвестный вариант выбора сервер отвергнет. */
export function normalizeApprovalRequest(
  raw: unknown,
): SSEApprovalRequestData | null {
  if (!raw || typeof raw !== "object") return null;
  const item = raw as Partial<SSEApprovalRequestData>;
  if (typeof item.request_id !== "string" || !item.request_id) return null;
  const choices = Array.isArray(item.choices)
    ? APPROVAL_CHOICE_ORDER.filter((choice) => item.choices?.includes(choice))
    : [];
  const decisionKind =
    item.decision_kind === "outbound_message" || item.decision_kind === "payment"
      ? item.decision_kind
      : undefined;
  const effectStatus = EFFECT_STATUSES.includes(item.effect_status as EffectDecisionStatus)
    ? (item.effect_status as EffectDecisionStatus)
    : undefined;
  return {
    ...item,
    request_id: item.request_id,
    ...(decisionKind ? { decision_kind: decisionKind } : { decision_kind: undefined }),
    ...(effectStatus ? { effect_status: effectStatus } : { effect_status: undefined }),
    // Пустой список означал бы карточку без единой кнопки. Такого сервер не
    // присылает, но если пришлёт — показываем минимум, который он принимает
    // всегда: разово разрешить или отклонить.
    choices: choices.length > 0 ? choices : ["once", "deny"],
  };
}

/** Запросы и история решений этого чата. Пустой ответ — нормальное состояние. */
export async function fetchPendingApprovals(
  sessionId: string,
  profile?: string,
): Promise<SSEApprovalRequestData[]> {
  if (!sessionId) return [];
  const url = withProfile(
    `/api/chat/approvals?session_id=${encodeURIComponent(sessionId)}`,
    profile,
  );
  const response = await authedFetch(url);
  if (!response.ok) {
    throw new Error(`${response.status}`);
  }
  const body = (await response.json()) as { data?: unknown };
  if (!Array.isArray(body?.data)) return [];
  return body.data
    .map(normalizeApprovalRequest)
    .filter((item): item is SSEApprovalRequestData => item !== null);
}

/** Durable external effects for the whole selected profile, across chats. */
export async function fetchEffectDecisions(
  profile?: string,
): Promise<SSEApprovalRequestData[]> {
  const response = await authedFetch(withProfile("/api/chat/decisions", profile));
  if (!response.ok) throw new Error(`${response.status}`);
  const body = (await response.json()) as { data?: unknown };
  if (!Array.isArray(body?.data)) return [];
  return body.data
    .map(normalizeApprovalRequest)
    .filter(
      (item): item is SSEApprovalRequestData =>
        item !== null && Boolean(item.decision_kind) && Boolean(item.source_session_id),
    );
}

export interface ApprovalDecisionResult {
  ok: boolean;
  /** Решать уже нечего: ход кончился или истёк таймаут ожидания ответа.
   *  Карточку в этом случае надо убрать, а не предлагать повтор. */
  expired: boolean;
  /** Текст для человека. Пусто при успехе. */
  error: string;
  /** Durable external-effect state returned after an exact decision. */
  effectStatus?: Exclude<EffectDecisionStatus, "pending">;
}

/** Отправить одно решение. Ошибку не бросаем: карточке нужен не стек, а
 *  ответ на вопрос «повторять или карточка уже мертва». */
export async function sendApprovalDecision(options: {
  sessionId: string;
  requestId: string;
  choice: ApprovalChoiceValue;
  profile?: string;
}): Promise<ApprovalDecisionResult> {
  const { sessionId, requestId, choice, profile } = options;
  if (!sessionId || !requestId) {
    return { ok: false, expired: true, error: "Запрос уже не адресуем." };
  }
  let response: Response;
  try {
    response = await authedFetch(withProfile("/api/chat/approval", profile), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        request_id: requestId,
        choice,
      }),
    });
  } catch {
    return {
      ok: false,
      expired: false,
      error: "Связь прервалась — решение не ушло. Повторите.",
    };
  }
  if (response.ok) {
    let effectStatus: ApprovalDecisionResult["effectStatus"];
    try {
      const body = (await response.json()) as { effect_status?: unknown };
      const value = body?.effect_status;
      if (
        value === "approved" || value === "denied" || value === "executing" ||
        value === "succeeded" || value === "failed" || value === "unknown"
      ) {
        effectStatus = value;
      }
    } catch {
      // Legacy command approvals have no response body field to inspect.
    }
    return {
      ok: true,
      expired: false,
      error: "",
      ...(effectStatus ? { effectStatus } : {}),
    };
  }
  if (response.status === 409) {
    // Движок отвечает 409 и на «слушателя уже нет», и на «в очереди пусто» —
    // для человека это один и тот же итог: отвечать больше некому.
    return {
      ok: false,
      expired: true,
      error: "Агент больше не ждёт ответа: ход закончился или истекло время.",
    };
  }
  let serverError = "";
  try {
    const body = (await response.json()) as { error?: { message?: unknown } };
    if (typeof body?.error?.message === "string") serverError = body.error.message;
  } catch {
    // Keep the stable local fallback below for non-JSON proxy errors.
  }
  return {
    ok: false,
    expired: false,
    error: serverError || "Решение не отправилось — попробуйте ещё раз.",
  };
}
