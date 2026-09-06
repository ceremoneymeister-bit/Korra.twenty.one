/**
 * Чистые функции мастера создания агента.
 *
 * Владелец пишет по-русски: имя агента и чем он занимается. Движку нужны
 * латинский идентификатор профиля, текст SOUL.md (единственное, что попадает
 * в системный промпт — описание из profile.yaml агент не видит) и короткое
 * описание для карточки и подсказки вкладки. Всё это выводится отсюда, чтобы
 * мастер не спрашивал человека о технических деталях и чтобы правила вывода
 * проверялись тестами, а не глазами.
 */

/** Та же грамматика имени, что у движка (`_PROFILE_ID_RE` в profiles.py). */
export const PROFILE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

/** Имена, которые движок отвергает (`_RESERVED_NAMES` + подкоманды CLI в
 *  profiles.py). Дублируем, чтобы подсказать замену до запроса, а не после
 *  ответа 400; сервер остаётся последней инстанцией. */
const RESERVED_IDS = new Set([
  "hermes",
  "korra",
  "default",
  "test",
  "tmp",
  "root",
  "sudo",
  "chat",
  "model",
  "gateway",
  "setup",
  "whatsapp",
  "login",
  "logout",
  "status",
  "cron",
  "doctor",
  "dump",
  "config",
  "pairing",
  "skills",
  "tools",
  "mcp",
  "sessions",
  "insights",
  "version",
  "update",
  "uninstall",
  "profile",
  "plugins",
  "honcho",
  "acp",
]);

/** Транслит по ГОСТ 7.79 (упрощённый, как в адресах сайтов): «щ» → shch,
 *  «ё» → yo, «й» → y, мягкий и твёрдый знаки опускаются. */
const TRANSLIT: Record<string, string> = {
  а: "a",
  б: "b",
  в: "v",
  г: "g",
  д: "d",
  е: "e",
  ё: "yo",
  ж: "zh",
  з: "z",
  и: "i",
  й: "y",
  к: "k",
  л: "l",
  м: "m",
  н: "n",
  о: "o",
  п: "p",
  р: "r",
  с: "s",
  т: "t",
  у: "u",
  ф: "f",
  х: "kh",
  ц: "ts",
  ч: "ch",
  ш: "sh",
  щ: "shch",
  ъ: "",
  ы: "y",
  ь: "",
  э: "e",
  ю: "yu",
  я: "ya",
  // Украинские и белорусские буквы, встречающиеся в русских именах.
  і: "i",
  ї: "yi",
  є: "ye",
  ґ: "g",
  ў: "u",
};

const MAX_ID_LENGTH = 64;

/**
 * Идентификатор профиля из человеческого имени.
 *
 * «Учитель китайского» → `uchitel-kitayskogo`, «SMM-менеджер» → `smm-menedzher`.
 * Всё, что не буква и не цифра, становится дефисом; дефисы не дублируются и не
 * стоят по краям. Пусто, если из имени не осталось ни одного латинского знака
 * (например, одни эмодзи) — тогда мастер просит задать системное имя руками.
 */
export function slugFromDisplayName(displayName: string): string {
  const lower = displayName.trim().toLowerCase();
  let out = "";
  for (const ch of lower) {
    if (/[a-z0-9]/.test(ch)) out += ch;
    else if (ch in TRANSLIT) out += TRANSLIT[ch];
    else out += "-";
  }
  out = out.replace(/-+/g, "-").replace(/^-+|-+$/g, "");
  if (out.length > MAX_ID_LENGTH) {
    out = out.slice(0, MAX_ID_LENGTH).replace(/-+$/g, "");
  }
  return out;
}

/**
 * Свободный идентификатор среди уже существующих профилей.
 *
 * Занятое имя получает числовой хвост: `sekretar` → `sekretar-2` → `sekretar-3`.
 * Зарезервированные движком слова тоже считаются занятыми — иначе человек,
 * назвавший агента «Профиль», получил бы 400 от сервера вместо агента.
 */
export function uniqueProfileId(base: string, taken: Iterable<string>): string {
  const used = new Set(Array.from(taken, (name) => name.trim().toLowerCase()));
  const isFree = (candidate: string) =>
    candidate !== "" && !used.has(candidate) && !RESERVED_IDS.has(candidate);
  if (isFree(base)) return base;
  if (base === "") return "";
  for (let n = 2; n < 1000; n += 1) {
    const suffix = `-${n}`;
    const trunk = base.slice(0, MAX_ID_LENGTH - suffix.length).replace(/-+$/g, "");
    const candidate = `${trunk}${suffix}`;
    if (isFree(candidate)) return candidate;
  }
  return "";
}

/** Проверка идентификатора перед отправкой: пусто — нет ошибки (поле ещё не
 *  заполнено), иначе текст для подписи под полем или `null`, если всё в порядке. */
export function profileIdProblem(id: string, taken: Iterable<string>): string | null {
  const value = id.trim();
  if (!value) return null;
  if (!PROFILE_ID_RE.test(value)) {
    return "Только строчные латинские буквы, цифры, «-» и «_»; первый знак — буква или цифра; до 64 знаков.";
  }
  if (RESERVED_IDS.has(value)) {
    return "Это имя занято системой — выберите другое.";
  }
  const used = new Set(Array.from(taken, (name) => name.trim().toLowerCase()));
  if (used.has(value)) return "Агент с таким системным именем уже есть.";
  return null;
}

/** Предел короткого описания (карточка, подсказка вкладки, канбан). */
const DESCRIPTION_LIMIT = 160;

/**
 * Короткое описание из текста роли — первое предложение, не длиннее 160 знаков.
 *
 * Отдельного поля «описание» мастер не спрашивает: человек уже написал, чем
 * занимается агент, второй раз то же самое просить незачем.
 */
export function descriptionFromRole(role: string): string {
  const text = role.replace(/\s+/g, " ").trim();
  if (!text) return "";
  const match = /^(.+?[.!?…])(?:\s|$)/.exec(text);
  let sentence = (match ? match[1] : text).trim();
  if (sentence.length > DESCRIPTION_LIMIT) {
    const cut = sentence.slice(0, DESCRIPTION_LIMIT - 1);
    const lastSpace = cut.lastIndexOf(" ");
    sentence = `${(lastSpace > 40 ? cut.slice(0, lastSpace) : cut).trim()}…`;
  }
  return sentence;
}

/**
 * Правила общения по умолчанию — `DEFAULT_SOUL_MD` движка по-русски.
 *
 * Английский дефолт делает каждого нового агента «Korra, be direct»; для
 * владельца-предпринимателя те же правила нужны на его языке, но глобальный
 * шаблон CLI мы не переводим (решено с Астрой 05.09): русский текст
 * собирается здесь и уходит в SOUL.md буквально.
 */
export const DEFAULT_CONDUCT_RU =
  "Отвечай по-русски и по существу: короткий вопрос — короткий ответ, " +
  "законченная работа — краткий отчёт (что сделано, что проверено, что " +
  "осталось). Без воды и повторов. Соглашайся потому, что это верно, а не " +
  "потому, что так сказал собеседник; если не уверен — скажи прямо.";

/**
 * Текст SOUL.md для нового агента.
 *
 * Имя и роль — слова владельца как есть; правила общения — общий русский
 * хвост. Пустая роль даёт только имя и правила: агент хотя бы знает, как его
 * зовут, а не представляется «Korra, ИИ-агент для кода».
 */
export function composeSoul(displayName: string, role: string): string {
  const name = displayName.trim();
  const body = role.trim();
  const parts = [`# ${name}`, soulIntro(name)];
  if (body) parts.push(body);
  parts.push("## Как общаться", DEFAULT_CONDUCT_RU);
  return `${parts.join("\n\n")}\n`;
}

/** Первая строка роли по нашему шаблону — по ней же узнаём шаблон при
 *  переименовании. */
function soulIntro(name: string): string {
  return `Ты — ${name}, агент в системе Korra.`;
}

/**
 * Роль начинается с нашего шаблона для этого имени («# Имя» и «Ты — Имя,
 * агент в системе Korra.»).
 *
 * Переименование вкладки меняет лишь `display_name` в profile.yaml, а в
 * SOUL.md остаётся «Ты — {старое имя}», и агент представляется старым именем
 * (аудит 06.09, F13). Роль из интерфейса не переписываем (решение с Астрой
 * 06.09) — только предупреждаем, и лишь когда имя в роли действительно наше
 * шаблонное, а не случайное слово в чужих инструкциях.
 */
export function soulNamedAs(soul: string, name: string): boolean {
  const who = name.trim();
  if (!who) return false;
  const normalized = soul.replace(/\r\n/g, "\n").replace(/^\uFEFF/, "");
  return normalized.startsWith(`# ${who}\n\n${soulIntro(who)}`);
}

/**
 * Английские тексты SOUL.md, которые движок подкладывает сам
 * (`korra_cli/default_soul.py`: `DEFAULT_SOUL_MD` и `_LEGACY_TEMPLATE_SOULS`).
 * Совпадение означает, что роль агенту никто не задавал: он «Korra, be direct»
 * — как «Секретарь» на контуре владельца (аудит 06.09, F11).
 */
const ENGINE_DEFAULT_SOUL_STARTS = [
  "You are Korra. Be direct:",
  "You are Hermes Agent, built by Nous Research. Be direct:",
  "You are Hermes Agent, an intelligent AI assistant created by Nous Research.",
  "# Hermes Agent Persona",
];

/** Роль не задана: в SOUL.md лежит дефолт движка, пустота или его
 *  комментарий-заготовка. Точный текст движка дублировать не нужно —
 *  достаточно узнаваемого начала, а любой абзац владельца сверх него делает
 *  текст «настоящей» ролью. */
export function isEngineDefaultSoul(soul: string | null | undefined): boolean {
  const text = (soul ?? "").replace(/\r\n/g, "\n").replace(/^\uFEFF/, "").trim();
  if (!text) return true;
  if (text.startsWith("# Hermes Agent Persona")) {
    // Заготовка старых установщиков: заголовок и HTML-комментарий, ничего
    // своего. Любой текст вне комментария — уже роль.
    const rest = text
      .replace(/^# Hermes Agent Persona/, "")
      .replace(/<!--[\s\S]*?-->/g, "")
      .trim();
    return rest === "";
  }
  const start = ENGINE_DEFAULT_SOUL_STARTS.find((prefix) => text.startsWith(prefix));
  if (!start) return false;
  // Дефолт движка — один абзац. Второй абзац — уже слова владельца.
  return !/\n\s*\n\s*\S/.test(text.slice(start.length));
}

/** Заготовка роли: имя агента и текст, который человек может взять как есть
 *  или поправить. */
export interface RoleStarter {
  id: string;
  name: string;
  role: string;
}

/**
 * Заготовки ролей для мастера — «с чего начать», когда не знаешь, что писать.
 *
 * Каждая обещает только то, что агент умеет из коробки: читать, уточнять,
 * считать, готовить текст, держать в голове сказанное в разговоре. Никаких
 * «подключённой почты», «календаря» и «напомню сам» — этого SOUL.md не даёт
 * (замечание Астры 05.09, распространено на заготовки 06.09).
 */
export const ROLE_STARTERS: readonly RoleStarter[] = [
  {
    id: "secretary",
    name: "Секретарь",
    role:
      "Помогаешь мне вести дела: составляешь и правишь письма, готовишь короткие " +
      "ответы, приводишь в порядок заметки после встреч. Договорённости, о " +
      "которых я рассказал в разговоре, держишь списком и показываешь по " +
      "запросу. Если не хватает данных — спрашивай, факты не выдумывай.",
  },
  {
    id: "requests",
    name: "Помощник по заявкам",
    role:
      "Помогаешь разбирать заявки клиентов. Уточняешь количество, сроки и бюджет. " +
      "Готовишь ответ клиенту в вежливом деловом тоне, спорные вопросы передаёшь мне.",
  },
  {
    id: "sales",
    name: "Продавец-консультант",
    role:
      "Отвечаешь на вопросы клиентов о наших товарах и условиях так, как я тебе " +
      "объяснил. Уточняешь потребность, предлагаешь подходящий вариант, цены " +
      "называешь только из моих данных. Если чего-то не знаешь — так и говоришь и " +
      "передаёшь вопрос мне.",
  },
  {
    id: "accounting",
    name: "Помощник бухгалтера",
    role:
      "Помогаешь с расчётами и документами: проверяешь цифры в том, что я " +
      "присылаю, считаешь суммы и сроки по правилам, которые я задам, готовишь " +
      "понятные пояснения. Спорное помечаешь для проверки бухгалтером и не " +
      "даёшь гарантий по налогам и закону.",
  },
  {
    id: "teacher",
    name: "Учитель китайского",
    role:
      "Учишь меня китайскому короткими занятиями по 15 минут: объясняешь простыми " +
      "словами, даёшь упражнения, проверяешь ответы и мягко исправляешь ошибки. " +
      "Объяснения — по-русски, примеры — с пиньинем и переводом.",
  },
  {
    id: "content",
    name: "Контент-менеджер",
    role:
      "Пишешь посты и тексты для нашего бизнеса по моим тезисам: предлагаешь " +
      "два-три варианта, держишь стиль, который я задам, без канцелярита и " +
      "штампов. Сам ничего не публикуешь — только готовишь текст.",
  },
];

/** Почему проверка не прошла — в словах владельца и с верным следующим шагом. */
export interface ProbeFailureAdvice {
  kind: "access" | "busy" | "timeout" | "network" | "config" | "unknown";
  /** Короткий заголовок рядом с пометкой «не отвечает». */
  title: string;
  /** Что делать; всегда напоминает, что сам агент сохранён. */
  advice: string;
  /** Показывать ли кнопку «Открыть „Ключи“». */
  keys: boolean;
}

/**
 * Разобрать отказ контрольного сообщения.
 *
 * Раньше любой отказ объяснялся ключом провайдера. 06.09 провайдер владельца
 * отвечал 401 «OAuth access token has expired» и 503 «all accounts are
 * rate-limited» — это подписка и лимит, а не ключ и не агент: имя, роль и
 * модель на диске уже есть. Человек должен видеть эту разницу, иначе он идёт
 * чинить то, что не сломано.
 */
export function explainProbeFailure(
  ...texts: Array<string | null | undefined>
): ProbeFailureAdvice {
  const text = texts.filter(Boolean).join(" \n ");
  const saved = "Сам агент сохранён: имя, роль и модель на месте.";
  if (/не удалось связаться с сервером|failed to fetch|networkerror|load failed/i.test(text)) {
    return {
      kind: "network",
      title: "Панель не достучалась до сервера",
      advice: `${saved} Проверьте интернет и повторите проверку.`,
      keys: false,
    };
  }
  // Движок не знает провайдера у этого профиля: настройки провайдера не
  // доехали до его config.yaml/.env (живьём 06.09: главный агент на Codex,
  // новому выбрали dario — «Unknown provider 'custom:dario'»). Это не ключ и
  // не подписка: лечится повторным выбором модели у агента.
  if (/unknown provider|no llm provider configured|no credentials found|provider .* not configured/i.test(text)) {
    return {
      kind: "config",
      title: "Провайдер не подключён к агенту",
      advice:
        `${saved} Настройки выбранного провайдера не перенеслись в агента. ` +
        "Откройте «Модель» в меню вкладки и выберите модель ещё раз; если не " +
        "поможет — проверьте «Ключи» этого агента.",
      keys: true,
    };
  }
  if (/\b(401|403)\b|unauthori[sz]ed|forbidden|expired|invalid (x-)?api[- ]key|re-?authenticate|auth cool-?down|authentication/i.test(text)) {
    return {
      kind: "access",
      title: "Нет доступа к модели",
      advice:
        `${saved} Провайдер не принял ключ или подписку — проверьте их в «Ключах» ` +
        "или продлите подписку, затем повторите проверку.",
      keys: true,
    };
  }
  if (/\b(429|503|502)\b|rate[- ]?limit|too many requests|overloaded|cool-?down|temporarily unavailable|capacity/i.test(text)) {
    return {
      kind: "busy",
      title: "Провайдер перегружен или исчерпан лимит",
      advice: `${saved} Повторите проверку через несколько минут.`,
      keys: false,
    };
  }
  if (/\b504\b|time[d]? ?out|deadline/i.test(text)) {
    return {
      kind: "timeout",
      title: "Модель не ответила вовремя",
      advice: `${saved} Повторите проверку; если повторяется — выберите другую модель.`,
      keys: false,
    };
  }
  return {
    kind: "unknown",
    title: "Агент не ответил",
    advice:
      `${saved} Повторите проверку; если ответа нет — посмотрите ключ провайдера ` +
      "в «Ключах» или выберите другую модель.",
    keys: true,
  };
}
