/**
 * Данные предприятия: форма ввода и её перевод в пак движка.
 *
 * Экран редактирует не JSON, а строки таблиц — так их вводит человек. Здесь
 * лежит перевод в обе стороны и словари подписей. Всё чистое и покрыто
 * тестом: на экране ошибка в этом переводе выглядит как «просто цифра не
 * сохранилась», а стоит неверной цены в КП.
 *
 * Числа держим СТРОКАМИ на всём пути. Движок считает Decimal и принимает
 * строки; превратив «0.3» в JS-число, мы бы отдали ему 0.30000000000000004 —
 * и получили бы расхождение в копейках там, где его никто не ищет.
 */

/* ------------------------------------------------------------------ */
/*  Словари — зеркало закрытых списков движка                          */
/* ------------------------------------------------------------------ */

export const BLANK_OPS = [
  { code: "bandsaw", title: "Лентопил" },
  { code: "pipe_cut", title: "Труборез" },
  { code: "laser", title: "Лазерная резка" },
  { code: "plasma", title: "Плазменная резка" },
  { code: "waterjet", title: "Гидроабразив" },
  { code: "bench", title: "Слесарная обработка" },
] as const;

export const MACHINE_OPS = [
  { code: "turning", title: "Токарная" },
  { code: "milling", title: "Фрезерная" },
  { code: "drilling", title: "Сверлильная" },
  { code: "edm", title: "Эрозия" },
  { code: "grinding", title: "Шлифовка" },
] as const;

export const EXTRA_CODES = [
  { code: "packaging", title: "Упаковка" },
  { code: "logistics", title: "Доставка" },
] as const;

/** Единица, в которой берётся ставка операции заготовки. */
export const RATE_KINDS = [
  { value: "per_cut_m", title: "за метр реза" },
  { value: "per_cut", title: "за рез" },
  { value: "per_hour", title: "за час" },
] as const;

export const STOCK_KINDS = [
  { value: "stocked", title: "есть на складе" },
  { value: "purchase", title: "под заказ (звёздочка)" },
] as const;

export const MARGIN_BASIS = [
  { value: "on_cost", title: "наценка на себестоимость" },
  { value: "on_price", title: "маржа в цене" },
] as const;

export const ROUNDING = [
  { value: "none", title: "без округления" },
  { value: "half_up", title: "до рубля, 50 копеек вверх" },
  { value: "up_10", title: "вверх до 10 ₽" },
  { value: "up_100", title: "вверх до 100 ₽" },
  { value: "bankers", title: "банковское" },
] as const;

/** Источник денежной цифры. Пустое значение по умолчанию — намеренно. */
export const MONEY_SOURCE_KINDS = [
  { value: "client_canon", title: "Прайс предприятия" },
  { value: "supplier_quote", title: "КП поставщика" },
] as const;

/** Источник коэффициента нормирования. Выдача модели источником не является. */
export const NORM_SOURCE_KINDS = [
  { value: "handbook", title: "Справочник" },
  { value: "client_canon", title: "Канон предприятия" },
] as const;

export const TARIFF_UNITS = [
  "per_hour",
  "per_piece",
  "per_cut_m",
  "per_cut",
  "per_pierce",
  "per_bend",
  "per_kg",
  "per_m2",
  "per_km",
] as const;

/** Названия — только подписи формы; постоянная идентичность остаётся в code. */
export const PROCESS_TITLES: Record<string, string> = {
  "BLANK.CUTOFF": "Распил",
  "CUT.LASER.SHEET": "Лазерная резка",
  "CUT.LASER.TUBE": "Лазерная резка на труборезе",
  "CUT.WATERJET": "Гидроабразивная резка",
  "CUT.PLASMA": "Плазменная резка",
  "CUT.SHEAR.GUILLOTINE": "Гильотина",
  "MACHINING.MILL.PORTAL": "Фрезерный портальник",
  "MACHINING.MILL.VERTICAL": "Фрезерный вертикальный",
  "MACHINING.MILL.5_AXIS": "Фрезерный 5-осевой",
  "MACHINING.MILL.C_AXIS": "Фрезерный с осью С",
  "MACHINING.MILL.CNC": "Фрезерный ЧПУ",
  "MACHINING.TURN.LONG_BED_10M": "Токарный 10-метровый",
  "MACHINING.TURN.LONG_BED_5M": "Токарный 5-метровый",
  "MACHINING.TURN.VERTICAL": "Токарный карусельный",
  "MACHINING.TURN.C_AXIS": "Токарный с осью С",
  "MACHINING.TURN.CNC": "Токарный ЧПУ",
  "MACHINING.TURN.MANUAL": "Токарный универсал",
  "MACHINING.GRIND.GENERAL": "Шлифовка",
  "MACHINING.EDM.GENERAL": "Эрозия",
  "MACHINING.EDM.HOLE_DRILL": "Эрозия-дрель",
  "FORM.ROLL": "Вальцовка",
  "FORM.TUBE_BEND": "Трубогиб",
  "FORM.PRESS_BEND": "Гибка",
  "FINISH.POWDER_COAT": "Порошковое окрашивание",
  "FINISH.LIQUID_PAINT": "Малярка",
  "JOIN.WELD": "Сварочные работы",
  "FINISH.FITTER": "Слесарка",
  "ASSEMBLY.GENERAL": "Сборочные работы",
  "FORM.WIRE_BEND": "Проволокогиб",
};

/**
 * Какие параметры режима нужны каждой операции.
 *
 * Дословная копия `NORM_FIELDS_BY_OP` из `metal_calc/packs2.py`. Копия
 * существует только чтобы нарисовать форму; судьёй остаётся сервер — при
 * публикации он сверяет набор ключей на точное равенство и отвергнет любое
 * расхождение. Менять здесь, не меняя там, бессмысленно: не опубликуется.
 */
export const NORM_FIELDS: Record<string, { key: string; label: string; unit: string }[]> = {
  turning: [
    { key: "cutting_speed_m_min", label: "Скорость резания", unit: "м/мин" },
    { key: "feed_mm_rev", label: "Подача", unit: "мм/об" },
    { key: "depth_mm", label: "Глубина", unit: "мм" },
  ],
  // У сверления глубины за проход нет: сверло идёт насквозь с непрерывной
  // подачей. Поле стояло здесь и в движке, методолог его заполняла — а на
  // время оно не влияло никак.
  drilling: [
    { key: "cutting_speed_m_min", label: "Скорость резания", unit: "м/мин" },
    { key: "feed_mm_rev", label: "Подача", unit: "мм/об" },
  ],
  milling: [
    { key: "feed_table_mm_min", label: "Подача стола", unit: "мм/мин" },
    { key: "depth_mm", label: "Глубина", unit: "мм" },
  ],
  edm: [{ key: "removal_min_per_cm2", label: "Съём", unit: "мин/см²" }],
  grinding: [{ key: "removal_min_per_cm2", label: "Съём", unit: "мин/см²" }],
};

/* ------------------------------------------------------------------ */
/*  Типы формы                                                         */
/* ------------------------------------------------------------------ */

export interface SourceForm {
  kind: string;
  ref: string;
  as_of: string;
  /** Метаданные источника из совместимой новой схемы нельзя терять. */
  extra?: Record<string, unknown>;
}

export interface BlankOpRow {
  code: string;
  enabled: boolean;
  rate_kind: string;
  rate_rub: string;
  source: SourceForm;
}

export interface MaterialRow {
  /** Ключ строки в паке. Его же агент передаёт в material_code. */
  code: string;
  grade: string;
  group: string;
  stock: string;
  density_kg_m3: string;
  rate_rub_per_kg: string;
  source: SourceForm;
}

export interface MachineRow {
  code: string;
  enabled: boolean;
  rate_rub_per_hour: string;
  source: SourceForm;
}

export interface NormRow {
  op_code: string;
  material_group: string;
  values: Record<string, string>;
  source: SourceForm;
}

export interface ExtraRow {
  code: string;
  enabled: boolean;
  rate_rub: string;
  source: SourceForm;
}

export interface AxisMaximumRow {
  axis_name: string;
  maximum: string;
}

export interface ProcessParkRow {
  process_code: string;
  rate_id: string;
  supplementary_rate_ids: string[];
  method_code: string;
  requires_manual_review?: boolean;
  note: string;
  in_house_axis_max: AxisMaximumRow[];
  /** Поля более новой совместимой схемы возвращаются без потерь. */
  extra: Record<string, unknown>;
}

export interface MatrixAxisRow {
  /** Ключ оси в расчётном драйвере; название показывается формой рядом. */
  name: string;
  unit: string;
  edges: string[];
  open_end: boolean;
  extra: Record<string, unknown>;
}

interface RateRowBase {
  rate_id: string;
  tariff_unit: string;
  vat_included: boolean;
  source: SourceForm;
  extra: Record<string, unknown>;
}

export interface ScalarRateRow extends RateRowBase {
  kind: "scalar";
  value: string;
}

export interface MatrixRateRow extends RateRowBase {
  kind: "matrix";
  axes: MatrixAxisRow[];
  grid: (string | null)[][];
}

export type RateRegistryRow = ScalarRateRow | MatrixRateRow;

export interface Schema4Issue {
  where: string;
  message: string;
}

export interface RatesForm {
  schema_version: number;
  process_park: ProcessParkRow[];
  rate_registry: RateRegistryRow[];
  /** Совместимые поля более новой схемы не исчезают после публикации. */
  extra: Record<string, unknown>;
  blank_ops: BlankOpRow[];
  materials: MaterialRow[];
  machines: MachineRow[];
  norms: NormRow[];
  overheads: {
    t_aux_min: string;
    k_service_rest_pct: string;
    t_setup_min: string;
    source: SourceForm;
  };
  extras: ExtraRow[];
  pricing: {
    margin_basis: string;
    margin_percent: string;
    material_markup_percent: string;
    rates_include_vat: boolean;
    vat_included: boolean;
    vat_rate_pct: string;
    valid_days: string;
    rounding: string;
  };
}

export interface ActiveRatesResponse {
  revision: string | null;
  sha256?: string;
  activated_at?: string;
  activated_by?: string;
  pack: Record<string, unknown> | null;
  configured: boolean;
}

export interface RevisionEntry {
  revision: string;
  at: string | null;
  author: string | null;
  note: string;
  sha256: string | null;
  active: boolean;
  journaled: boolean;
}

export interface RevisionsResponse {
  active: string | null;
  revisions: RevisionEntry[];
  warning: string | null;
}

/* ------------------------------------------------------------------ */
/*  Пустая форма и перевод из пака                                     */
/* ------------------------------------------------------------------ */

export function emptySource(): SourceForm {
  // Вид источника пуст намеренно: значение по умолчанию человек проматывает
  // не глядя, а источник цены — то, что потом отвечает за неё.
  return { kind: "", ref: "", as_of: today(), extra: {} };
}

export function today(): string {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function emptyForm(): RatesForm {
  return {
    schema_version: 2,
    process_park: [],
    rate_registry: [],
    extra: {},
    blank_ops: BLANK_OPS.map((op) => ({
      code: op.code,
      enabled: false,
      rate_kind: "per_cut",
      rate_rub: "",
      source: emptySource(),
    })),
    materials: [],
    machines: MACHINE_OPS.map((op) => ({
      code: op.code,
      enabled: false,
      rate_rub_per_hour: "",
      source: emptySource(),
    })),
    norms: [],
    overheads: {
      t_aux_min: "",
      k_service_rest_pct: "",
      t_setup_min: "",
      source: emptySource(),
    },
    extras: EXTRA_CODES.map((extra) => ({
      code: extra.code,
      enabled: false,
      rate_rub: "",
      source: emptySource(),
    })),
    pricing: {
      margin_basis: "on_cost",
      margin_percent: "",
      material_markup_percent: "",
      rates_include_vat: false,
      vat_included: true,
      vat_rate_pct: "20",
      valid_days: "14",
      rounding: "none",
    },
  };
}

function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : String(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function unknownFields(
  value: Record<string, unknown>,
  known: readonly string[],
): Record<string, unknown> {
  const blocked = new Set(known);
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !blocked.has(key))
      .map(([key, item]) => [key, structuredClone(item)]),
  );
}

function processRowsFrom(value: unknown): ProcessParkRow[] {
  return Object.entries(asRecord(value)).map(([process_code, rawValue]) => {
    const raw = asRecord(rawValue);
    const maxima = asRecord(raw.in_house_axis_max);
    return {
      process_code,
      rate_id: asText(raw.rate_id),
      supplementary_rate_ids: Array.isArray(raw.supplementary_rate_ids)
        ? raw.supplementary_rate_ids.map(asText)
        : [],
      method_code: asText(raw.method_code),
      requires_manual_review:
        typeof raw.requires_manual_review === "boolean"
          ? raw.requires_manual_review
          : undefined,
      note: asText(raw.note),
      in_house_axis_max: Object.entries(maxima).map(([axis_name, maximum]) => ({
        axis_name,
        maximum: asText(maximum),
      })),
      extra: unknownFields(raw, [
        "rate_id",
        "supplementary_rate_ids",
        "method_code",
        "requires_manual_review",
        "note",
        "in_house_axis_max",
      ]),
    };
  });
}

function rateRowsFrom(value: unknown): RateRegistryRow[] {
  return Object.entries(asRecord(value)).flatMap<RateRegistryRow>(([rate_id, rawValue]) => {
    const raw = asRecord(rawValue);
    const common = {
      rate_id,
      tariff_unit: asText(raw.tariff_unit),
      vat_included: raw.vat_included === true,
      source: sourceFrom(raw.source),
    };
    if (raw.kind === "scalar") {
      return [
        {
          ...common,
          kind: "scalar" as const,
          value: asText(raw.value),
          extra: unknownFields(raw, [
            "kind",
            "tariff_unit",
            "vat_included",
            "source",
            "value",
          ]),
        },
      ];
    }
    if (raw.kind !== "matrix") return [];
    const axes = Array.isArray(raw.axes)
      ? raw.axes.map((axisValue) => {
          const axis = asRecord(axisValue);
          return {
            name: asText(axis.name),
            unit: asText(axis.unit),
            edges: Array.isArray(axis.edges) ? axis.edges.map(asText) : [],
            open_end: axis.open_end === true,
            extra: unknownFields(axis, ["name", "unit", "edges", "open_end"]),
          };
        })
      : [];
    const grid = Array.isArray(raw.grid)
      ? raw.grid.map((row) =>
          Array.isArray(row)
            ? row.map((cell) => (cell === null ? null : asText(cell)))
            : [],
        )
      : [];
    return [
      {
        ...common,
        kind: "matrix" as const,
        axes,
        grid,
        extra: unknownFields(raw, [
          "kind",
          "tariff_unit",
          "vat_included",
          "source",
          "axes",
          "grid",
        ]),
      },
    ];
  });
}

function sourceFrom(value: unknown): SourceForm {
  const raw = asRecord(value);
  return {
    kind: asText(raw.kind),
    ref: asText(raw.ref),
    as_of: asText(raw.as_of) || today(),
    extra: unknownFields(raw, ["kind", "ref", "as_of"]),
  };
}

/** Разложить опубликованный пак обратно в строки формы. */
export function formFromPack(pack: Record<string, unknown> | null): RatesForm {
  const form = emptyForm();
  if (!pack) return form;

  const schemaVersion = Number(pack.schema_version ?? 2);
  form.schema_version = Number.isInteger(schemaVersion) ? schemaVersion : 2;
  form.process_park = processRowsFrom(pack.process_park);
  form.rate_registry = rateRowsFrom(pack.rate_registry);
  form.extra = unknownFields(pack, [
    "schema_version",
    "revision",
    "status",
    "blank_ops",
    "materials",
    "machines",
    "norm_params",
    "overheads",
    "extras",
    "pricing",
    "process_park",
    "rate_registry",
  ]);

  const blank = (pack.blank_ops ?? {}) as Record<string, Record<string, unknown>>;
  form.blank_ops = BLANK_OPS.map((op) => {
    const row = blank[op.code];
    if (!row) {
      return { code: op.code, enabled: false, rate_kind: "per_cut", rate_rub: "", source: emptySource() };
    }
    return {
      code: op.code,
      enabled: true,
      rate_kind: asText(row.rate_kind) || "per_cut",
      rate_rub: asText(row.rate_rub),
      source: sourceFrom(row.rate_source),
    };
  });

  const materials = (pack.materials ?? {}) as Record<string, Record<string, unknown>>;
  form.materials = Object.entries(materials).map(([code, row]) => ({
    code,
    grade: asText(row.grade),
    group: asText(row.group),
    stock: asText(row.stock) || "stocked",
    density_kg_m3: asText(row.density_kg_m3),
    rate_rub_per_kg: asText(row.rate_rub_per_kg),
    source: sourceFrom(row.rate_source),
  }));

  const machines = (pack.machines ?? {}) as Record<string, Record<string, unknown>>;
  form.machines = MACHINE_OPS.map((op) => {
    const row = machines[op.code];
    if (!row) {
      return { code: op.code, enabled: false, rate_rub_per_hour: "", source: emptySource() };
    }
    return {
      code: op.code,
      enabled: true,
      rate_rub_per_hour: asText(row.rate_rub_per_hour),
      source: sourceFrom(row.rate_source),
    };
  });

  const norms = (pack.norm_params ?? {}) as Record<string, Record<string, unknown>>;
  form.norms = Object.entries(norms).map(([key, row]) => {
    const [op_code, ...rest] = key.split(":");
    const values: Record<string, string> = {};
    for (const field of NORM_FIELDS[op_code] ?? []) {
      values[field.key] = asText(row[field.key]);
    }
    return {
      op_code,
      material_group: rest.join(":"),
      values,
      source: sourceFrom(row.source),
    };
  });

  const overheads = (pack.overheads ?? {}) as Record<string, unknown>;
  form.overheads = {
    t_aux_min: asText(overheads.t_aux_min),
    k_service_rest_pct: asText(overheads.k_service_rest_pct),
    t_setup_min: asText(overheads.t_setup_min),
    source: sourceFrom(overheads.source),
  };

  const extras = (pack.extras ?? {}) as Record<string, Record<string, unknown>>;
  form.extras = EXTRA_CODES.map((extra) => {
    const row = extras[extra.code];
    if (!row) {
      return { code: extra.code, enabled: false, rate_rub: "", source: emptySource() };
    }
    return {
      code: extra.code,
      enabled: true,
      rate_rub: asText(row.rate_rub),
      source: sourceFrom(row.rate_source),
    };
  });

  const pricing = (pack.pricing ?? {}) as Record<string, unknown>;
  form.pricing = {
    margin_basis: asText(pricing.margin_basis) || "on_cost",
    margin_percent: asText(pricing.margin_percent),
    material_markup_percent: asText(pricing.material_markup_percent),
    rates_include_vat: pricing.rates_include_vat === true,
    vat_included: pricing.vat_included !== false,
    vat_rate_pct: asText(pricing.vat_rate_pct),
    valid_days: asText(pricing.valid_days) || "14",
    rounding: asText(pricing.rounding) || "none",
  };

  return form;
}

/* ------------------------------------------------------------------ */
/*  Перевод формы в пак                                                */
/* ------------------------------------------------------------------ */

function sourceToPack(source: SourceForm): Record<string, unknown> {
  return {
    ...structuredClone(source.extra ?? {}),
    kind: source.kind,
    ref: source.ref.trim(),
    as_of: source.as_of,
  };
}

function processParkToPack(rows: ProcessParkRow[]): Record<string, unknown> {
  return Object.fromEntries(
    rows.map((row) => {
      const entry: Record<string, unknown> = {
        ...structuredClone(row.extra),
        rate_id: row.rate_id.trim() || null,
      };
      const supplementary = row.supplementary_rate_ids
        .map((rateId) => rateId.trim())
        .filter(Boolean);
      if (supplementary.length) entry.supplementary_rate_ids = supplementary;
      if (row.method_code.trim()) entry.method_code = row.method_code.trim();
      if (row.requires_manual_review !== undefined) {
        entry.requires_manual_review = row.requires_manual_review;
      }
      if (row.note.trim()) entry.note = row.note.trim();
      if (row.in_house_axis_max.length) {
        entry.in_house_axis_max = Object.fromEntries(
          row.in_house_axis_max.map((maximum) => [
            maximum.axis_name.trim(),
            maximum.maximum.trim(),
          ]),
        );
      }
      return [row.process_code.trim(), entry];
    }),
  );
}

function rateRegistryToPack(rows: RateRegistryRow[]): Record<string, unknown> {
  return Object.fromEntries(
    rows.map((row) => {
      const base: Record<string, unknown> = {
        ...structuredClone(row.extra),
        kind: row.kind,
        tariff_unit: row.tariff_unit,
        vat_included: row.vat_included,
        source: sourceToPack(row.source),
      };
      if (row.kind === "scalar") {
        base.value = row.value.trim();
      } else {
        base.axes = row.axes.map((axis) => ({
          ...structuredClone(axis.extra),
          name: axis.name.trim(),
          unit: axis.unit.trim(),
          edges: axis.edges.map((edge) => edge.trim()),
          open_end: axis.open_end,
        }));
        base.grid = row.grid.map((gridRow) =>
          gridRow.map((cell) => (cell === null || !cell.trim() ? null : cell.trim())),
        );
      }
      return [row.rate_id.trim(), base];
    }),
  );
}

/**
 * Собрать пак из формы.
 *
 * Выключенные строки не попадают в пак вовсе — в этом смысл галочки: у
 * предприятия может не быть гидроабразива, и пустая ставка с нулём была бы
 * ложью, а не «нет данных».
 *
 * `revision` ставим заглушкой: настоящее имя присваивает CLI при публикации,
 * и оно единственное попадает в историю. Здесь оно нужно лишь потому, что
 * валидатор проверяет поле на месте.
 */
export function packFromForm(form: RatesForm): Record<string, unknown> {
  const blank_ops: Record<string, unknown> = {};
  for (const row of form.blank_ops) {
    if (!row.enabled) continue;
    blank_ops[row.code] = {
      rate_kind: row.rate_kind,
      rate_rub: row.rate_rub.trim(),
      rate_source: sourceToPack(row.source),
    };
  }

  const materials: Record<string, unknown> = {};
  for (const row of form.materials) {
    const code = row.code.trim();
    if (!code) continue;
    materials[code] = {
      grade: row.grade.trim(),
      group: row.group.trim(),
      stock: row.stock,
      density_kg_m3: row.density_kg_m3.trim(),
      rate_rub_per_kg: row.rate_rub_per_kg.trim(),
      rate_source: sourceToPack(row.source),
    };
  }

  const machines: Record<string, unknown> = {};
  for (const row of form.machines) {
    if (!row.enabled) continue;
    machines[row.code] = {
      rate_rub_per_hour: row.rate_rub_per_hour.trim(),
      rate_source: sourceToPack(row.source),
    };
  }

  const norm_params: Record<string, unknown> = {};
  for (const row of form.norms) {
    const group = row.material_group.trim();
    if (!row.op_code || !group) continue;
    const entry: Record<string, unknown> = { source: sourceToPack(row.source) };
    for (const field of NORM_FIELDS[row.op_code] ?? []) {
      entry[field.key] = (row.values[field.key] ?? "").trim();
    }
    norm_params[`${row.op_code}:${group}`] = entry;
  }

  const extras: Record<string, unknown> = {};
  for (const row of form.extras) {
    if (!row.enabled) continue;
    extras[row.code] = {
      rate_rub: row.rate_rub.trim(),
      rate_source: sourceToPack(row.source),
    };
  }

  const pack: Record<string, unknown> = {
    ...structuredClone(form.extra),
    schema_version: form.schema_version,
    revision: "draft",
    status: "active",
    blank_ops,
    materials,
    machines,
    norm_params,
    overheads: {
      t_aux_min: form.overheads.t_aux_min.trim(),
      k_service_rest_pct: form.overheads.k_service_rest_pct.trim(),
      t_setup_min: form.overheads.t_setup_min.trim(),
      source: sourceToPack(form.overheads.source),
    },
    extras,
    pricing: {
      margin_basis: form.pricing.margin_basis,
      margin_percent: form.pricing.margin_percent.trim(),
      ...(form.schema_version >= 4
        ? {
            material_markup_percent: form.pricing.material_markup_percent.trim(),
            rates_include_vat: form.pricing.rates_include_vat,
          }
        : {}),
      vat_included: form.pricing.vat_included,
      vat_rate_pct: form.pricing.vat_rate_pct.trim(),
      valid_days: Number.parseInt(form.pricing.valid_days, 10) || 0,
      rounding: form.pricing.rounding,
    },
  };
  if (form.schema_version >= 3) {
    pack.process_park = processParkToPack(form.process_park);
    pack.rate_registry = rateRegistryToPack(form.rate_registry);
  }
  return pack;
}

/** Группы материалов, которые реально заведены — подсказка для норм. */
export function materialGroups(form: RatesForm): string[] {
  const groups = new Set<string>();
  for (const row of form.materials) {
    const group = row.group.trim();
    if (group) groups.add(group);
  }
  return [...groups].sort();
}

export function matrixShape(rate: MatrixRateRow): {
  rows: number;
  columns: number;
} {
  const intervals = (axis: MatrixAxisRow | undefined) =>
    axis ? Math.max(0, axis.edges.length - 1 + (axis.open_end ? 1 : 0)) : 0;
  return {
    rows: intervals(rate.axes[0]),
    columns: rate.axes.length > 1 ? intervals(rate.axes[1]) : 1,
  };
}

/** Меняет размер сетки после правки осей, сохраняя совпадающие ячейки. */
export function resizeMatrixGrid(rate: MatrixRateRow): MatrixRateRow {
  const { rows, columns } = matrixShape(rate);
  return {
    ...rate,
    grid: Array.from({ length: rows }, (_, rowIndex) =>
      Array.from(
        { length: columns },
        (_, columnIndex) => rate.grid[rowIndex]?.[columnIndex] ?? null,
      ),
    ),
  };
}

export function availableRateIds(form: RatesForm): string[] {
  const ids = new Set(form.rate_registry.map((row) => row.rate_id.trim()).filter(Boolean));
  form.blank_ops.forEach((row) => row.enabled && ids.add(`blank:${row.code}`));
  form.materials.forEach((row) => row.code.trim() && ids.add(`material:${row.code.trim()}`));
  form.machines.forEach((row) => row.enabled && ids.add(`machine:${row.code}`));
  form.extras.forEach((row) => row.enabled && ids.add(`extra:${row.code}`));
  return [...ids].sort();
}

export function processTitle(processCode: string): string {
  return PROCESS_TITLES[processCode] ?? "Название уточнит валидатор каталога";
}

export function rateTitle(rateId: string, processes: ProcessParkRow[] = []): string {
  const owner = processes.find((process) => process.rate_id.trim() === rateId.trim());
  if (owner) return processTitle(owner.process_code);
  const local = rateId.split(":").at(-1)?.replace(/[._-]+/g, " ").trim();
  return local ? local[0].toUpperCase() + local.slice(1) : "Новая ставка";
}

export function axisTitle(axisName: string): string {
  const known: Record<string, string> = {
    thickness_mm: "Толщина",
    length_mm: "Длина",
    width_mm: "Ширина",
    diameter_mm: "Диаметр",
    cuts_count: "Количество резов",
    grade_code: "Категория работ",
    material_group_code: "Группа материала",
  };
  return known[axisName] ?? (axisName || "Ось без ключа");
}

export function newScalarRate(): ScalarRateRow {
  return {
    kind: "scalar",
    rate_id: "",
    tariff_unit: "per_hour",
    vat_included: false,
    source: emptySource(),
    value: "",
    extra: {},
  };
}

export function newMatrixRate(): MatrixRateRow {
  return {
    kind: "matrix",
    rate_id: "",
    tariff_unit: "per_piece",
    vat_included: false,
    source: emptySource(),
    axes: [
      { name: "", unit: "", edges: ["0", "1"], open_end: false, extra: {} },
    ],
    grid: [[null]],
    extra: {},
  };
}

export function newProcessParkRow(): ProcessParkRow {
  return {
    process_code: "",
    rate_id: "",
    supplementary_rate_ids: [],
    method_code: "",
    note: "",
    in_house_axis_max: [],
    extra: {},
  };
}

const RATE_ID = /^[a-z0-9][a-z0-9._-]{0,31}(:[a-z0-9][a-z0-9._-]{0,31})?$/;
const PROCESS_CODE = /^[A-Z0-9]+(?:[._][A-Z0-9]+)+$/;
const DECIMAL = /^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$/;

function positiveDecimal(value: string): boolean {
  return DECIMAL.test(value.trim()) && Number(value) > 0;
}

function completeSource(source: SourceForm): boolean {
  return Boolean(
    source.kind && source.ref.trim() && /^\d{4}-\d{2}-\d{2}$/.test(source.as_of),
  );
}

/** Немедленные структурные проверки schema4; движок остаётся финальным судьёй. */
export function validateSchema4(form: RatesForm): Schema4Issue[] {
  if (form.schema_version < 3) return [];
  const issues: Schema4Issue[] = [];
  const rateIds = new Set<string>();
  const explicitRates = new Map<string, RateRegistryRow>();

  form.rate_registry.forEach((rate, index) => {
    const id = rate.rate_id.trim();
    const where = `Ставка ${id || index + 1}`;
    if (!RATE_ID.test(id)) {
      issues.push({ where, message: "ID ставки: строчные латинские буквы, цифры, . _ - и один двоеточие" });
    } else if (/^(machine|material|blank|extra):/.test(id)) {
      issues.push({ where, message: "этот префикс занят ставками обычных разделов" });
    } else if (rateIds.has(id)) {
      issues.push({ where, message: "такой ID ставки уже есть" });
    } else {
      rateIds.add(id);
      explicitRates.set(id, rate);
    }
    if (!(TARIFF_UNITS as readonly string[]).includes(rate.tariff_unit)) {
      issues.push({ where, message: "выберите единицу тарифа из списка" });
    }
    if (!completeSource(rate.source)) {
      issues.push({ where, message: "заполните вид, ссылку и дату источника" });
    }
    if (rate.kind === "scalar") {
      if (!positiveDecimal(rate.value)) {
        issues.push({ where, message: "ставка должна быть числом больше нуля" });
      }
      return;
    }

    if (rate.axes.length < 1 || rate.axes.length > 2) {
      issues.push({ where, message: "у матрицы должна быть одна или две оси" });
    }
    const axisNames = new Set<string>();
    rate.axes.forEach((axis, axisIndex) => {
      const axisWhere = `${where} · ось ${axisIndex + 1}`;
      const name = axis.name.trim();
      if (!name || name.length > 32) {
        issues.push({ where: axisWhere, message: "ключ оси обязателен, до 32 символов" });
      } else if (axisNames.has(name)) {
        issues.push({ where: axisWhere, message: "ключи осей должны различаться" });
      } else {
        axisNames.add(name);
      }
      if (axis.edges.length < 2 || axis.edges.length > 32) {
        issues.push({ where: axisWhere, message: "нужно от 2 до 32 границ" });
      }
      const numbers = axis.edges.map((edge) =>
        DECIMAL.test(edge.trim()) ? Number(edge) : Number.NaN,
      );
      if (numbers.some((value) => !Number.isFinite(value))) {
        issues.push({ where: axisWhere, message: "каждая граница должна быть числом" });
      } else if (numbers.some((value, itemIndex) => itemIndex > 0 && value <= numbers[itemIndex - 1])) {
        issues.push({ where: axisWhere, message: "границы должны строго возрастать" });
      }
    });
    const shape = matrixShape(rate);
    if (rate.grid.length !== shape.rows) {
      issues.push({ where, message: `в сетке ожидается ${shape.rows} строки` });
    }
    rate.grid.forEach((gridRow, rowIndex) => {
      if (gridRow.length !== shape.columns) {
        issues.push({
          where: `${where} · строка ${rowIndex + 1}`,
          message: `ожидается ${shape.columns} ячеек`,
        });
      }
      gridRow.forEach((cell, columnIndex) => {
        if (cell !== null && !positiveDecimal(cell)) {
          issues.push({
            where: `${where} · ячейка ${rowIndex + 1}:${columnIndex + 1}`,
            message: "значение должно быть числом больше нуля или пустым",
          });
        }
      });
    });
    if (!rate.grid.some((gridRow) => gridRow.some((cell) => cell !== null))) {
      issues.push({ where, message: "заполните хотя бы одну ячейку матрицы" });
    }
  });

  const allRateIds = new Set(availableRateIds(form));
  const processCodes = new Set<string>();
  form.process_park.forEach((process, index) => {
    const code = process.process_code.trim();
    const where = `Процесс ${code || index + 1}`;
    if (!PROCESS_CODE.test(code)) {
      issues.push({ where, message: "укажите process_code заглавными латинскими сегментами" });
    } else if (processCodes.has(code)) {
      issues.push({ where, message: "такой process_code уже есть" });
    } else {
      processCodes.add(code);
    }
    if (process.rate_id && !allRateIds.has(process.rate_id.trim())) {
      issues.push({ where, message: `основная ставка ${process.rate_id} не найдена` });
    }
    if (process.supplementary_rate_ids.length > 8) {
      issues.push({ where, message: "дополнительных ставок может быть не больше восьми" });
    }
    const links = new Set<string>();
    process.supplementary_rate_ids.forEach((rawId) => {
      const id = rawId.trim();
      if (!allRateIds.has(id)) {
        issues.push({ where, message: `дополнительная ставка ${id || "без ID"} не найдена` });
      } else if (id === process.rate_id.trim() || links.has(id)) {
        issues.push({ where, message: `ставка ${id} повторяется в связях процесса` });
      }
      links.add(id);
    });
    if (process.supplementary_rate_ids.length && !process.rate_id.trim()) {
      issues.push({ where, message: "дополнительным ставкам нужна основная ставка" });
    }
    if (process.note.length > 200) {
      issues.push({ where, message: "примечание должно быть не длиннее 200 символов" });
    }
    const primary = explicitRates.get(process.rate_id.trim());
    const axisNames = new Set(
      primary?.kind === "matrix" ? primary.axes.map((axis) => axis.name.trim()) : [],
    );
    const maximumNames = new Set<string>();
    process.in_house_axis_max.forEach((maximum) => {
      const name = maximum.axis_name.trim();
      if (primary?.kind !== "matrix") {
        issues.push({ where, message: "пределы осей требуют основную матричную ставку" });
      } else if (!axisNames.has(name)) {
        issues.push({ where, message: `оси ${name || "без ключа"} нет в основной ставке` });
      } else if (maximumNames.has(name)) {
        issues.push({ where, message: `предел оси ${name} указан дважды` });
      }
      if (!positiveDecimal(maximum.maximum)) {
        issues.push({ where, message: `максимум оси ${name || "?"} должен быть числом больше нуля` });
      }
      maximumNames.add(name);
    });
  });
  if (!form.process_park.length) {
    issues.push({ where: "Парк процессов", message: "добавьте хотя бы один процесс" });
  }
  return issues;
}

export function formatMoment(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
