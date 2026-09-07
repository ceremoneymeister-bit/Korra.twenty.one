/**
 * Проверка формы данных предприятия — только форма, не бизнес-правила.
 *
 * Граница проведена намеренно. Здесь ловится незаполненность: пустая ставка,
 * не число, дата не датой, недоделанный источник, две строки с одним ключом.
 * Это то, что нужно человеку СРАЗУ, у поля, пока он печатает.
 *
 * Всё содержательное — НДС не больше ста процентов, маржа `on_price` меньше
 * ста, набор параметров режима под операцию, закрытые списки операций —
 * остаётся движку и проверяется при публикации дословным `_validate_pack2`.
 * Продублировать эти правила здесь означало бы завести второй свод, который
 * однажды разойдётся с первым и будет пропускать или запрещать не то.
 *
 * Отсюда же следует, что «Проверить» всегда бьёт на сервер, даже когда тут
 * всё зелёное: зелёное здесь означает «форму можно отправлять», а не «данные
 * верны».
 */

import { NORM_FIELDS, type RatesForm, type SourceForm } from "./calc-rates";

export interface FormIssue {
  /** Адрес строки: `materials.09g2s`, `machines.turning`, `pricing`. */
  where: string;
  message: string;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Положительное число в строке. Разделитель — точка: так его ждёт движок. */
export function isPositiveNumber(value: string): boolean {
  const text = (value ?? "").trim();
  if (!text || !/^\d+(\.\d+)?$/.test(text)) return false;
  return Number.parseFloat(text) > 0;
}

/** Неотрицательное: накладные и доп. расходы законно бывают нулём. */
export function isNonNegativeNumber(value: string): boolean {
  const text = (value ?? "").trim();
  if (!text || !/^\d+(\.\d+)?$/.test(text)) return false;
  return Number.parseFloat(text) >= 0;
}

/**
 * Полон ли источник.
 *
 * Три поля вместе или ничего: цифра без источника — это цифра, за которую
 * потом некому ответить, а ровно на такие в КП и смотрит заказчик.
 */
export function isSourceComplete(source: SourceForm | undefined): boolean {
  if (!source) return false;
  return Boolean(source.kind) && Boolean(source.ref?.trim()) && ISO_DATE.test(source.as_of ?? "");
}

function checkSource(where: string, source: SourceForm, issues: FormIssue[]): void {
  if (!source.kind) {
    issues.push({ where, message: "не выбран вид источника" });
  }
  if (!source.ref?.trim()) {
    issues.push({ where, message: "не указан источник (издание и страница либо номер прайса)" });
  } else if (source.ref.length > 256) {
    issues.push({ where, message: "источник длиннее 256 символов" });
  }
  if (!ISO_DATE.test(source.as_of ?? "")) {
    issues.push({ where, message: "не указана дата источника" });
  }
}

/** Сколько строк оставлено без полного источника — счётчик в шапке экрана. */
export function countMissingSources(form: RatesForm): number {
  let missing = 0;
  for (const row of form.blank_ops) {
    if (row.enabled && !isSourceComplete(row.source)) missing += 1;
  }
  for (const row of form.materials) {
    if (!isSourceComplete(row.source)) missing += 1;
  }
  for (const row of form.machines) {
    if (row.enabled && !isSourceComplete(row.source)) missing += 1;
  }
  for (const row of form.norms) {
    if (!isSourceComplete(row.source)) missing += 1;
  }
  for (const row of form.extras) {
    if (row.enabled && !isSourceComplete(row.source)) missing += 1;
  }
  if (!isSourceComplete(form.overheads.source)) missing += 1;
  return missing;
}

/**
 * Минимальный набор, с которым уже можно работать.
 *
 * Написано в шапке экрана словами: без этого человек ждёт, пока заполнит
 * «всё», и не публикует ничего неделю.
 */
export function hasMinimumData(form: RatesForm): boolean {
  return (
    form.blank_ops.some((row) => row.enabled) &&
    form.materials.length > 0 &&
    form.machines.some((row) => row.enabled)
  );
}

export function validateForm(form: RatesForm): FormIssue[] {
  const issues: FormIssue[] = [];

  for (const row of form.blank_ops) {
    if (!row.enabled) continue;
    const where = `Заготовка · ${row.code}`;
    if (!isPositiveNumber(row.rate_rub)) {
      issues.push({ where, message: "ставка должна быть числом больше нуля" });
    }
    checkSource(where, row.source, issues);
  }

  const codes = new Set<string>();
  for (const row of form.materials) {
    const code = row.code.trim();
    const where = `Материал · ${code || "без кода"}`;
    if (!code) {
      issues.push({ where, message: "не заполнен код материала" });
    } else if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(code)) {
      // Код уезжает в material_code инструмента и в ключ пака — пробелы и
      // кириллица там ломают ссылку молча.
      issues.push({ where, message: "код: латиница, цифры, дефис — без пробелов" });
    } else if (codes.has(code)) {
      issues.push({ where, message: "такой код уже есть — коды должны различаться" });
    } else {
      codes.add(code);
    }
    if (!row.grade.trim()) issues.push({ where, message: "не заполнена марка" });
    if (!row.group.trim()) issues.push({ where, message: "не заполнена группа" });
    if (!isPositiveNumber(row.density_kg_m3)) {
      issues.push({ where, message: "плотность должна быть числом больше нуля" });
    }
    if (!isPositiveNumber(row.rate_rub_per_kg)) {
      issues.push({ where, message: "цена за килограмм должна быть числом больше нуля" });
    }
    checkSource(where, row.source, issues);
  }

  for (const row of form.machines) {
    if (!row.enabled) continue;
    const where = `Станок · ${row.code}`;
    if (!isPositiveNumber(row.rate_rub_per_hour)) {
      issues.push({ where, message: "станко-час должен быть числом больше нуля" });
    }
    checkSource(where, row.source, issues);
  }

  const groups = new Set(form.materials.map((row) => row.group.trim()).filter(Boolean));
  const normKeys = new Set<string>();
  for (const row of form.norms) {
    const group = row.material_group.trim();
    const where = `Режим · ${row.op_code}:${group || "?"}`;
    if (!group) {
      issues.push({ where, message: "не указана группа материала" });
    } else if (!groups.has(group)) {
      // Движок не найдёт нормы для группы, которой нет ни у одного материала,
      // и упадёт уже в бою — на расчёте времени, а не здесь.
      issues.push({ where, message: "такой группы нет ни у одного материала" });
    }
    const key = `${row.op_code}:${group}`;
    if (normKeys.has(key)) {
      issues.push({ where, message: "режим для этой пары уже задан" });
    } else {
      normKeys.add(key);
    }
    for (const field of NORM_FIELDS[row.op_code] ?? []) {
      if (!isPositiveNumber(row.values[field.key] ?? "")) {
        issues.push({ where, message: `${field.label}: число больше нуля` });
      }
    }
    checkSource(where, row.source, issues);
  }

  const overheads = form.overheads;
  if (!isNonNegativeNumber(overheads.t_aux_min)) {
    issues.push({ where: "Накладные", message: "вспомогательное время: число" });
  }
  if (!isNonNegativeNumber(overheads.k_service_rest_pct)) {
    issues.push({ where: "Накладные", message: "процент на обслуживание и отдых: число" });
  }
  if (!isNonNegativeNumber(overheads.t_setup_min)) {
    issues.push({ where: "Накладные", message: "подготовительное время: число" });
  }
  checkSource("Накладные", overheads.source, issues);

  for (const row of form.extras) {
    if (!row.enabled) continue;
    const where = `Доп. расходы · ${row.code}`;
    if (!isNonNegativeNumber(row.rate_rub)) {
      issues.push({ where, message: "ставка должна быть числом" });
    }
    checkSource(where, row.source, issues);
  }

  const pricing = form.pricing;
  if (!isNonNegativeNumber(pricing.margin_percent)) {
    issues.push({ where: "Цена", message: "процент наценки: число" });
  }
  if (
    form.schema_version >= 4 &&
    !isNonNegativeNumber(pricing.material_markup_percent)
  ) {
    issues.push({ where: "Цена", message: "наценка на материал: число" });
  }
  if (!isNonNegativeNumber(pricing.vat_rate_pct)) {
    issues.push({ where: "Цена", message: "ставка НДС: число" });
  }
  const validDays = Number.parseInt(pricing.valid_days, 10);
  if (!Number.isInteger(validDays) || validDays < 1 || validDays > 365) {
    issues.push({ where: "Цена", message: "срок действия КП: от 1 до 365 дней" });
  }

  if (!hasMinimumData(form)) {
    issues.push({
      where: "Минимум",
      message: "нужны хотя бы одна операция заготовки, один материал и один станок",
    });
  }

  return issues;
}

/** Можно ли публиковать: форма собрана и у каждой цифры есть источник. */
export function canPublish(form: RatesForm): { ok: boolean; reason: string } {
  const missing = countMissingSources(form);
  if (missing > 0) {
    return {
      ok: false,
      reason: `Строк без источника: ${missing}. У каждой цифры должен быть источник и дата.`,
    };
  }
  const issues = validateForm(form);
  if (issues.length > 0) {
    return { ok: false, reason: `${issues[0].where}: ${issues[0].message}` };
  }
  return { ok: true, reason: "" };
}
