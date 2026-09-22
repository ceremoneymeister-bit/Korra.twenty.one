/**
 * useDictation — диктовка для поля ввода: запись с микрофона и распознавание
 * речи на стороне агента.
 *
 * Хук ничего не отправляет и не знает про чат: он отдаёт распознанный текст
 * через `onText`, а решение, что с ним делать, остаётся у поля ввода.
 * Остановка только по второму нажатию — распознавание запускает владелец,
 * а не таймер тишины: порог громкости нечем откалибровать, и «умная» пауза
 * рвала бы фразу на каждом вдохе. Единственный автоматический стоп —
 * десятиминутный предохранитель от забытого микрофона. Предел достаточно
 * велик для связного монолога и остаётся заметным в интерфейсе владельца.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { transcribeAudio } from "@/lib/api";
import { ownerFacingError } from "@/lib/owner-facing-error";

/** Что сейчас делает диктовка. */
export type DictationState =
  /** Покой: микрофон выключен. */
  | "idle"
  /** Браузер спрашивает доступ к микрофону. */
  | "starting"
  /** Идёт запись. */
  | "recording"
  /** Запись ушла на распознавание. */
  | "transcribing";

/** Форматы, которые понимает и `MediaRecorder`, и приёмник движка.
 *  Chrome/Firefox дают webm+opus, Safari умеет только mp4. */
const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/mpeg",
];

/** Сжатая браузерная запись укладывается в серверный предел 25 МБ с запасом. */
const MAX_RECORDING_SECONDS = 10 * 60;
const MAX_RECORDING_MS = MAX_RECORDING_SECONDS * 1_000;
/** Небольшие порции не заставляют браузер держать весь монолог внутри recorder. */
const RECORDING_CHUNK_MS = 1_000;

export interface UseDictationOptions {
  /** Профиль агента: у каждого свой ключ распознавания. */
  profile?: string;
  /** Распознанный текст — уже без крайних пробелов и непустой. */
  onText: (text: string) => void;
  /** Отказ, который стоит показать владельцу. Текст готов к выводу. */
  onError: (message: string) => void;
  /** Речи не слышно: сервер ответил успехом и пустой строкой. */
  onEmpty?: () => void;
}

export interface UseDictationReturn {
  state: DictationState;
  /** Видимый владельцу предохранитель одной браузерной записи. */
  recordingLimitSeconds: number;
  /** Микрофон доступен в этом браузере и на этом адресе. */
  supported: boolean;
  /** Почему диктовка недоступна; `null` — доступна. */
  unavailableReason: string | null;
  /** Покой → запись, запись → распознавание. В остальных состояниях ничего. */
  toggle: () => void;
  /** Бросить запись и отпустить микрофон, ничего не распознавая. */
  cancel: () => void;
}

interface SupportProbe {
  supported: boolean;
  reason: string | null;
}

function detectSupport(): SupportProbe {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return { supported: false, reason: "Диктовка недоступна в этом окружении" };
  }
  // Микрофон браузер отдаёт только в защищённом контексте: по http кнопка
  // честно остаётся выключенной, а не падает при нажатии.
  if (window.isSecureContext === false) {
    return { supported: false, reason: "Диктовка работает только по HTTPS" };
  }
  if (
    typeof MediaRecorder === "undefined" ||
    typeof navigator.mediaDevices?.getUserMedia !== "function"
  ) {
    return { supported: false, reason: "Браузер не умеет записывать звук" };
  }
  return { supported: true, reason: null };
}

function pickMimeType(): string | undefined {
  if (
    typeof MediaRecorder === "undefined" ||
    typeof MediaRecorder.isTypeSupported !== "function"
  ) {
    return undefined;
  }
  return MIME_CANDIDATES.find((candidate) =>
    MediaRecorder.isTypeSupported(candidate),
  );
}

/** Отказ `getUserMedia` — по-русски и по делу. */
function microphoneError(error: unknown): string {
  const name = (error as { name?: string } | null)?.name ?? "";
  switch (name) {
    case "NotAllowedError":
    case "PermissionDeniedError":
      return "Доступ к микрофону запрещён. Разрешите его в настройках браузера и повторите.";
    case "NotFoundError":
    case "DevicesNotFoundError":
    case "OverconstrainedError":
      return "Микрофон не найден. Подключите его и повторите.";
    case "NotReadableError":
    case "TrackStartError":
      return "Микрофон занят другой программой. Закройте её и повторите.";
    case "SecurityError":
      return "Браузер запретил микрофон на этом адресе.";
    default:
      return "Не удалось включить микрофон. Повторите попытку.";
  }
}

function stopTracks(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop());
}

export function useDictation({
  profile,
  onText,
  onError,
  onEmpty,
}: UseDictationOptions): UseDictationReturn {
  const [support] = useState(detectSupport);
  const [state, setState] = useState<DictationState>("idle");

  // Обработчики меняют личность на каждом рендере, а запись живёт дольше
  // рендера: держим их в ref, чтобы start/stop оставались стабильными.
  const handlersRef = useRef({ profile, onText, onError, onEmpty });
  useEffect(() => {
    handlersRef.current = { profile, onText, onError, onEmpty };
  });

  const stateRef = useRef<DictationState>("idle");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);
  const aliveRef = useRef(true);
  // Номер попытки. Отмена и размонтирование его увеличивают, и всё, что
  // прилетит от прошлой записи — событие `onstop`, ответ распознавания —
  // узнаёт себя по устаревшему номеру и молча выбрасывается.
  const runRef = useRef(0);

  const apply = useCallback((next: DictationState) => {
    stateRef.current = next;
    if (aliveRef.current) setState(next);
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const release = useCallback(() => {
    stopTracks(streamRef.current);
    streamRef.current = null;
    recorderRef.current = null;
  }, []);

  const finish = useCallback(
    async (recorder: MediaRecorder, run: number) => {
      clearTimer();
      const chunks = chunksRef.current;
      chunksRef.current = [];
      // Микрофон отпускаем сразу: индикатор записи в браузере не должен
      // гореть, пока идёт распознавание.
      release();
      if (runRef.current !== run) return;

      const type = recorder.mimeType || chunks[0]?.type || "audio/webm";
      const blob = new Blob(chunks, { type });
      const { profile: scope, onText: emit, onError: fail, onEmpty: silent } =
        handlersRef.current;

      if (blob.size === 0) {
        apply("idle");
        silent?.();
        return;
      }

      apply("transcribing");
      try {
        const text = await transcribeAudio(blob, type, scope);
        if (runRef.current !== run) return;
        const trimmed = text.trim();
        if (trimmed) emit(trimmed);
        else silent?.();
      } catch (error) {
        if (runRef.current !== run) return;
        fail(ownerFacingError(error, "Не удалось распознать речь. Повторите попытку."));
      } finally {
        if (runRef.current === run) apply("idle");
      }
    },
    [apply, clearTimer, release],
  );

  const stop = useCallback(() => {
    clearTimer();
    const recorder = recorderRef.current;
    if (!recorder) return;
    // `stop()` дособирает последний кусок и зовёт `onstop` — там и работа.
    if (recorder.state !== "inactive") recorder.stop();
  }, [clearTimer]);

  const cancel = useCallback(() => {
    if (stateRef.current === "idle") return;
    runRef.current += 1;
    clearTimer();
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    chunksRef.current = [];
    release();
    apply("idle");
  }, [apply, clearTimer, release]);

  const start = useCallback(async () => {
    if (!support.supported || stateRef.current !== "idle") return;
    const run = ++runRef.current;
    apply("starting");

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      if (runRef.current === run) {
        apply("idle");
        handlersRef.current.onError(microphoneError(error));
      }
      return;
    }
    // Пока браузер спрашивал доступ, владелец мог передумать.
    if (runRef.current !== run || !aliveRef.current) {
      stopTracks(stream);
      return;
    }

    const mimeType = pickMimeType();
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    } catch {
      stopTracks(stream);
      apply("idle");
      handlersRef.current.onError("Браузер не смог начать запись звука.");
      return;
    }

    chunksRef.current = [];
    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onstop = () => {
      void finish(recorder, run);
    };
    recorder.onerror = () => {
      if (runRef.current !== run) return;
      runRef.current += 1;
      clearTimer();
      chunksRef.current = [];
      release();
      apply("idle");
      handlersRef.current.onError("Запись прервалась. Повторите попытку.");
    };

    recorderRef.current = recorder;
    streamRef.current = stream;
    recorder.start(RECORDING_CHUNK_MS);
    apply("recording");
    timerRef.current = window.setTimeout(() => stop(), MAX_RECORDING_MS);
  }, [apply, clearTimer, finish, release, stop, support.supported]);

  const toggle = useCallback(() => {
    if (stateRef.current === "idle") void start();
    else if (stateRef.current === "recording") stop();
    // «starting» и «transcribing» короткие и кнопка на них занята.
  }, [start, stop]);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      runRef.current += 1;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = null;
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      chunksRef.current = [];
      stopTracks(streamRef.current);
      streamRef.current = null;
      recorderRef.current = null;
      stateRef.current = "idle";
    };
  }, []);

  return {
    state,
    recordingLimitSeconds: MAX_RECORDING_SECONDS,
    supported: support.supported,
    unavailableReason: support.reason,
    toggle,
    cancel,
  };
}
