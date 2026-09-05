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
  const parts = [`# ${name}`, `Ты — ${name}, агент в системе Korra.`];
  if (body) parts.push(body);
  parts.push("## Как общаться", DEFAULT_CONDUCT_RU);
  return `${parts.join("\n\n")}\n`;
}
