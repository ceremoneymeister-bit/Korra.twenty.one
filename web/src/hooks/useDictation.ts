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
 *
 * Надиктованное не теряется молча. Если распознавание не удалось (обрыв
 * связи, отказ сервера) или вернуло заметно меньше звука, чем было записано,
 * запись остаётся у хука: её можно распознать повторно или скачать.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { transcribeRecording } from "@/lib/api";
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

/** Запись, которую нельзя выбросить: владелец её надиктовал, а текста нет
 *  или он заметно короче записи. */
export interface KeptRecording {
  /** `failed` — текста нет, запись ждёт повтора; `partial` — текст уже в
   *  поле, но звука до сервера дошло заметно меньше, чем записано. */
  kind: "failed" | "partial";
  /** Длина записи по часам браузера, секунды. */
  recordedSeconds: number;
  /** Сколько звука нашёл сервер в принятом файле; `null` — не измерил. */
  audioSeconds: number | null;
  /** Причина по-русски, готова к выводу. */
  message: string;
  /** Ссылка на запись для скачивания; пустая — браузер не дал её создать. */
  url: string;
  /** Имя файла для скачивания. */
  fileName: string;
}

/** Форматы, которые понимает и `MediaRecorder`, и приёмник движка.
 *  Chrome, Firefox и Safari с 18.4 пишут webm+opus; старый Safari — только mp4. */
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
/** Пустой ответ на запись хотя бы такой длины — не «тишина», а подозрение на
 *  потерю: такую запись сохраняем, а не выбрасываем. */
const SILENT_KEEP_SECONDS = 15;
/** Запись считается дошедшей не целиком, если сервер нашёл звука меньше
 *  записанного больше чем на столько секунд и на такую долю. Браузер
 *  запускает рекордер с задержкой в доли секунды — это не потеря. */
const PARTIAL_MIN_GAP_SECONDS = 5;
const PARTIAL_MIN_SHARE = 0.1;

/** Сохранённые записи живут дольше поля ввода: оно пересоздаётся при смене
 *  чата, а карточка обещает владельцу, что запись есть в этой вкладке. */
interface KeptEntry {
  recording: Recording;
  view: KeptRecording;
}
const keptByKey = new Map<string, KeptEntry>();

export interface UseDictationOptions {
  /** Профиль агента: у каждого свой ключ распознавания. */
  profile?: string;
  /** Чей черновик: под этим ключом нераспознанная запись переживает
   *  пересоздание поля ввода (смену чата) до перезагрузки страницы. */
  keepKey?: string;
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
  /** Секунды идущей записи; во время распознавания — длина ушедшей записи. */
  elapsedSeconds: number;
  /** Сохранённая запись, которую владелец ещё не разобрал. */
  kept: KeptRecording | null;
  /** Микрофон доступен в этом браузере и на этом адресе. */
  supported: boolean;
  /** Почему диктовка недоступна; `null` — доступна. */
  unavailableReason: string | null;
  /** Покой → запись, запись → распознавание. В остальных состояниях ничего. */
  toggle: () => void;
  /** Бросить запись и отпустить микрофон, ничего не распознавая. */
  cancel: () => void;
  /** Распознать сохранённую неудачную запись ещё раз. */
  retry: () => void;
  /** Убрать сохранённую запись. */
  discard: () => void;
}

interface SupportProbe {
  supported: boolean;
  reason: string | null;
}

/** Готовая запись: звук и её длина по часам браузера. */
interface Recording {
  blob: Blob;
  type: string;
  recordedMs: number;
  endedAt: Date;
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

/** «5:07» — длина записи так, как её видит владелец. */
export function formatDictationClock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function recordingFileName(type: string, endedAt: Date): string {
  const ext = type.includes("mp4")
    ? "m4a"
    : type.includes("ogg")
      ? "ogg"
      : type.includes("mpeg")
        ? "mp3"
        : "webm";
  const pad = (value: number) => String(value).padStart(2, "0");
  const stamp =
    `${endedAt.getFullYear()}-${pad(endedAt.getMonth() + 1)}-${pad(endedAt.getDate())}` +
    `-${pad(endedAt.getHours())}${pad(endedAt.getMinutes())}`;
  return `диктовка-${stamp}.${ext}`;
}

function objectUrl(blob: Blob): string {
  return typeof URL !== "undefined" && typeof URL.createObjectURL === "function"
    ? URL.createObjectURL(blob)
    : "";
}

function keptEntry(
  recording: Recording,
  kind: KeptRecording["kind"],
  message: string,
  audioSeconds: number | null,
): KeptEntry {
  return {
    recording,
    view: {
      kind,
      recordedSeconds: recording.recordedMs / 1_000,
      audioSeconds,
      message,
      url: objectUrl(recording.blob),
      fileName: recordingFileName(recording.type, recording.endedAt),
    },
  };
}

function revokeUrl(url: string | undefined): void {
  if (url && typeof URL !== "undefined" && typeof URL.revokeObjectURL === "function") {
    URL.revokeObjectURL(url);
  }
}

export function useDictation({
  profile,
  keepKey,
  onText,
  onError,
  onEmpty,
}: UseDictationOptions): UseDictationReturn {
  const [support] = useState(detectSupport);
  const [state, setState] = useState<DictationState>("idle");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [kept, setKept] = useState<KeptRecording | null>(
    () => (keepKey ? keptByKey.get(keepKey)?.view ?? null : null),
  );

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
  const tickRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);
  const recordedMsRef = useRef<number | null>(null);
  const keptRef = useRef<KeptEntry | null>(keepKey ? keptByKey.get(keepKey) ?? null : null);
  const keepKeyRef = useRef(keepKey);
  // Запись, которая сейчас на распознавании: если поле закроют раньше, чем
  // придёт текст, она уходит в сохранённые, а не пропадает.
  const inFlightRef = useRef<Recording | null>(null);
  // Запись, которую оборвало закрытие поля: `onstop` придёт уже после
  // размонтирования и сложит её в сохранённые под ключом черновика.
  const orphanRunRef = useRef<number | null>(null);
  useEffect(() => {
    keepKeyRef.current = keepKey;
  }, [keepKey]);
  const aliveRef = useRef(true);
  // Номер попытки. Отмена и размонтирование его увеличивают, и всё, что
  // прилетит от прошлой записи — событие `onstop`, ответ распознавания —
  // узнаёт себя по устаревшему номеру и молча выбрасывается.
  const runRef = useRef(0);

  const apply = useCallback((next: DictationState) => {
    stateRef.current = next;
    if (aliveRef.current) setState(next);
  }, []);

  const clearTimers = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (tickRef.current !== null) {
      window.clearInterval(tickRef.current);
      tickRef.current = null;
    }
  }, []);

  const release = useCallback(() => {
    stopTracks(streamRef.current);
    streamRef.current = null;
    recorderRef.current = null;
  }, []);

  const dropKept = useCallback(() => {
    revokeUrl(keptRef.current?.view.url);
    keptRef.current = null;
    if (keepKeyRef.current) keptByKey.delete(keepKeyRef.current);
    if (aliveRef.current) setKept(null);
  }, []);

  const keep = useCallback(
    (
      recording: Recording,
      kind: KeptRecording["kind"],
      message: string,
      audioSeconds: number | null,
    ) => {
      revokeUrl(keptRef.current?.view.url);
      keptRef.current = keptEntry(recording, kind, message, audioSeconds);
      if (keepKeyRef.current) keptByKey.set(keepKeyRef.current, keptRef.current);
      if (aliveRef.current) setKept(keptRef.current.view);
    },
    [],
  );

  const transcribe = useCallback(
    async (recording: Recording, run: number) => {
      const recordedSeconds = recording.recordedMs / 1_000;
      if (aliveRef.current) setElapsedSeconds(Math.round(recordedSeconds));
      apply("transcribing");
      const { profile: scope, onText: emit, onEmpty: silent } = handlersRef.current;
      inFlightRef.current = recording;
      try {
        const result = await transcribeRecording(recording.blob, {
          mimeType: recording.type,
          profile: scope,
          recordedMs: recording.recordedMs,
        });
        if (runRef.current !== run) return;
        inFlightRef.current = null;
        // Сохранённая запись (это был повтор) разобрана; если текст неполный
        // или пустой, ниже она сохранится заново.
        if (keptRef.current) dropKept();
        const text = result.text.trim();
        if (!text) {
          if (recordedSeconds >= SILENT_KEEP_SECONDS) {
            keep(
              recording,
              "failed",
              "Распознавание вернуло пустой текст. Повторите или скачайте запись.",
              result.audioSeconds,
            );
          } else {
            silent?.();
          }
          return;
        }
        emit(text);
        const audio = result.audioSeconds;
        const missing = audio === null ? 0 : recordedSeconds - audio;
        if (
          audio !== null &&
          missing > PARTIAL_MIN_GAP_SECONDS &&
          missing > recordedSeconds * PARTIAL_MIN_SHARE
        ) {
          keep(
            recording,
            "partial",
            `Распознано ${formatDictationClock(audio)} из ${formatDictationClock(recordedSeconds)}: ` +
              "остальное до сервера не дошло. Текст уже в поле, запись можно скачать.",
            audio,
          );
        }
      } catch (error) {
        if (runRef.current !== run) return;
        inFlightRef.current = null;
        keep(
          recording,
          "failed",
          ownerFacingError(error, "Не удалось распознать речь. Повторите попытку."),
          null,
        );
      } finally {
        if (runRef.current === run) apply("idle");
      }
    },
    [apply, dropKept, keep],
  );

  const finish = useCallback(
    async (recorder: MediaRecorder, run: number) => {
      clearTimers();
      const chunks = chunksRef.current;
      chunksRef.current = [];
      const recordedMs = recordedMsRef.current ?? Date.now() - startedAtRef.current;
      recordedMsRef.current = null;
      // Микрофон отпускаем сразу: индикатор записи в браузере не должен
      // гореть, пока идёт распознавание.
      release();
      const type = recorder.mimeType || chunks[0]?.type || "audio/webm";
      const blob = new Blob(chunks, { type });
      if (runRef.current !== run) {
        const key = keepKeyRef.current;
        if (orphanRunRef.current === run && key && blob.size > 0) {
          keptByKey.set(
            key,
            keptEntry(
              { blob, type, recordedMs, endedAt: new Date() },
              "failed",
              "Запись остановилась, когда закрылось поле ввода. Распознайте её здесь.",
              null,
            ),
          );
        }
        if (orphanRunRef.current === run) orphanRunRef.current = null;
        return;
      }

      if (blob.size === 0) {
        apply("idle");
        handlersRef.current.onEmpty?.();
        return;
      }
      await transcribe({ blob, type, recordedMs, endedAt: new Date() }, run);
    },
    [apply, clearTimers, release, transcribe],
  );

  const stop = useCallback(() => {
    clearTimers();
    const recorder = recorderRef.current;
    if (!recorder) return;
    recordedMsRef.current = Date.now() - startedAtRef.current;
    // `stop()` отдаёт весь записанный звук одним куском и зовёт `onstop` —
    // там и работа.
    if (recorder.state !== "inactive") recorder.stop();
  }, [clearTimers]);

  const cancel = useCallback(() => {
    if (stateRef.current === "idle") return;
    runRef.current += 1;
    clearTimers();
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    chunksRef.current = [];
    recordedMsRef.current = null;
    release();
    apply("idle");
  }, [apply, clearTimers, release]);

  const start = useCallback(async () => {
    if (!support.supported || stateRef.current !== "idle") return;
    // Нераспознанную запись новая не вытесняет: сначала владелец решает её
    // судьбу. Неполная уже отдала текст в поле — её место занимает новая.
    if (keptRef.current?.view.kind === "failed") return;
    if (keptRef.current) dropKept();
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
    recordedMsRef.current = null;
    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onstop = () => {
      void finish(recorder, run);
    };
    recorder.onerror = () => {
      if (runRef.current !== run) return;
      runRef.current += 1;
      clearTimers();
      chunksRef.current = [];
      release();
      apply("idle");
      handlersRef.current.onError("Запись прервалась. Повторите попытку.");
    };

    recorderRef.current = recorder;
    streamRef.current = stream;
    // Без timeslice: браузер отдаёт запись одним куском при остановке. С
    // timeslice Safari раз в секунду сбрасывает кодер и режет кластер WebM;
    // без него записи Виктории в Safari до hotfix 22.09 доходили до
    // распознавания целиком (до тогдашнего предела в две минуты).
    recorder.start();
    startedAtRef.current = Date.now();
    setElapsedSeconds(0);
    apply("recording");
    timerRef.current = window.setTimeout(() => stop(), MAX_RECORDING_MS);
    tickRef.current = window.setInterval(() => {
      if (aliveRef.current) {
        setElapsedSeconds(Math.floor((Date.now() - startedAtRef.current) / 1_000));
      }
    }, 1_000);
  }, [apply, clearTimers, dropKept, finish, release, stop, support.supported]);

  const toggle = useCallback(() => {
    if (stateRef.current === "idle") void start();
    else if (stateRef.current === "recording") stop();
    // «starting» и «transcribing» короткие и кнопка на них занята.
  }, [start, stop]);

  const retry = useCallback(() => {
    const saved = keptRef.current;
    if (!saved || saved.view.kind !== "failed" || stateRef.current !== "idle") return;
    const run = ++runRef.current;
    // Карточку прячем на время попытки, но запись держим, пока не придёт
    // текст: закроют поле посреди повтора — она останется сохранённой.
    if (aliveRef.current) setKept(null);
    void transcribe(saved.recording, run);
  }, [transcribe]);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      // Поле закрывается (смена чата): надиктованное не выбрасываем, а
      // оставляем под ключом черновика — владелец найдёт его, вернувшись.
      const key = keepKeyRef.current;
      const orphaned = Boolean(key) && stateRef.current === "recording";
      const inFlight = stateRef.current === "transcribing" ? inFlightRef.current : null;
      if (orphaned) {
        orphanRunRef.current = runRef.current;
        recordedMsRef.current = Date.now() - startedAtRef.current;
      }
      aliveRef.current = false;
      runRef.current += 1;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      if (tickRef.current !== null) window.clearInterval(tickRef.current);
      timerRef.current = null;
      tickRef.current = null;
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      if (!orphaned) chunksRef.current = [];
      stopTracks(streamRef.current);
      streamRef.current = null;
      recorderRef.current = null;
      if (inFlight && key && !keptByKey.has(key)) {
        keptByKey.set(
          key,
          keptEntry(
            inFlight,
            "failed",
            "Распознавание прервалось: поле ввода закрылось раньше, чем пришёл текст.",
            null,
          ),
        );
      }
      if (!key) revokeUrl(keptRef.current?.view.url);
      keptRef.current = null;
      stateRef.current = "idle";
    };
  }, []);

  return {
    state,
    recordingLimitSeconds: MAX_RECORDING_SECONDS,
    elapsedSeconds,
    kept,
    supported: support.supported,
    unavailableReason: support.reason,
    toggle,
    cancel,
    retry,
    discard: dropKept,
  };
}
