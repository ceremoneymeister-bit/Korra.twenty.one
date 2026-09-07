/**
 * Данные предприятия: перевод формы в пак и правила её проверки.
 *
 * Здесь проверяется то, что на экране выглядит одинаково при верном и
 * неверном поведении, а стоит неверной цены в КП.
 */

import { describe, expect, it } from "vitest";

import {
  ROUNDING,
  emptyForm,
  formFromPack,
  matrixShape,
  materialGroups,
  packFromForm,
  resizeMatrixGrid,
  validateSchema4,
  type MatrixRateRow,
  type RatesForm,
} from "./calc-rates";
import {
  canPublish,
  countMissingSources,
  isPositiveNumber,
  isSourceComplete,
  validateForm,
} from "./calc-rates-validate";

const MONEY = { kind: "client_canon", ref: "прайс от 20.08", as_of: "2026-08-20" };
const HANDBOOK = { kind: "handbook", ref: "Справочник, т.2, с.610", as_of: "2026-08-20" };

function filled(): RatesForm {
  const form = emptyForm();
  form.blank_ops[0] = {
    code: "bandsaw",
    enabled: true,
    rate_kind: "per_cut",
    rate_rub: "150",
    source: { ...MONEY },
  };
  form.materials = [
    {
      code: "steel-40x",
      grade: "40Х",
      group: "steel",
      stock: "purchase",
      density_kg_m3: "7850",
      rate_rub_per_kg: "95",
      source: { ...MONEY },
    },
  ];
  form.machines[0] = {
    code: "turning",
    enabled: true,
    rate_rub_per_hour: "3000",
    source: { ...MONEY },
  };
  form.norms = [
    {
      op_code: "turning",
      material_group: "steel",
      values: { cutting_speed_m_min: "180", feed_mm_rev: "0.3", depth_mm: "2" },
      source: { ...HANDBOOK },
    },
  ];
  form.overheads = {
    t_aux_min: "2",
    k_service_rest_pct: "8",
    t_setup_min: "20",
    source: { ...HANDBOOK },
  };
  form.pricing.margin_percent = "20";
  return form;
}

describe("packFromForm", () => {
  it("предлагает обычное коммерческое округление отдельно от банковского", () => {
    expect(ROUNDING).toContainEqual({
      value: "half_up",
      title: "до рубля, 50 копеек вверх",
    });
    expect(ROUNDING).toContainEqual({ value: "bankers", title: "банковское" });
  });

  it("числа остаются строками — Decimal у движка, а не float у браузера", () => {
    // «0.3» через JS-число даёт 0.30000000000000004 и расхождение в копейках
    // там, где его никто не ищет.
    const pack = packFromForm(filled());
    const norms = pack.norm_params as Record<string, Record<string, unknown>>;
    expect(norms["turning:steel"].feed_mm_rev).toBe("0.3");
    const materials = pack.materials as Record<string, Record<string, unknown>>;
    expect(materials["steel-40x"].rate_rub_per_kg).toBe("95");
  });

  it("выключенные строки не попадают в пак", () => {
    // Пустая ставка с нулём была бы утверждением «стоит ноль», а не
    // «у нас такой операции нет».
    const pack = packFromForm(filled());
    expect(Object.keys(pack.blank_ops as object)).toEqual(["bandsaw"]);
    expect(Object.keys(pack.machines as object)).toEqual(["turning"]);
    expect(pack.extras).toEqual({});
  });

  it("собирает ключ режима из операции и группы", () => {
    const pack = packFromForm(filled());
    expect(Object.keys(pack.norm_params as object)).toEqual(["turning:steel"]);
  });

  it("кладёт в режим только поля своей операции", () => {
    const form = filled();
    form.norms[0] = {
      op_code: "edm",
      material_group: "steel",
      // Значения от прошлой операции остались в состоянии — в пак они уйти
      // не должны: валидатор проверяет набор ключей на точное равенство.
      values: { removal_min_per_cm2: "0.8", feed_mm_rev: "0.3" },
      source: { ...HANDBOOK },
    };
    const norms = packFromForm(form).norm_params as Record<string, Record<string, unknown>>;
    expect(Object.keys(norms["edm:steel"]).sort()).toEqual(["removal_min_per_cm2", "source"]);
  });

  it("срок действия КП уходит числом — движок ждёт int", () => {
    const pricing = packFromForm(filled()).pricing as Record<string, unknown>;
    expect(pricing.valid_days).toBe(14);
  });
});

describe("formFromPack", () => {
  it("возвращает форму в том же виде, в каком её сохранили", () => {
    const pack = packFromForm(filled());
    const restored = packFromForm(formFromPack(pack));
    expect(restored).toEqual(pack);
  });

  it("пустой пак открывает пустую форму, а не падает", () => {
    expect(formFromPack(null).materials).toEqual([]);
  });

  it("не понижает enterprise pack и не теряет парк/реестр v4", () => {
    const source = packFromForm(filled());
    source.schema_version = 4;
    source.process_park = {
      "CUT.LASER.SHEET": {
        rate_id: "svc:laser-sheet",
        supplementary_rate_ids: ["svc:laser-pierce"],
      },
    };
    source.rate_registry = {
      "svc:laser-pierce": {
        kind: "scalar",
        tariff_unit: "per_pierce",
        vat_included: false,
        source: { ...MONEY },
        value: "7",
      },
    };
    (source.pricing as Record<string, unknown>).material_markup_percent = "15";
    (source.pricing as Record<string, unknown>).rates_include_vat = true;

    const restored = packFromForm(formFromPack(source));
    expect(restored.schema_version).toBe(4);
    expect(restored.process_park).toEqual(source.process_park);
    expect(restored.rate_registry).toEqual(source.rate_registry);
    expect(restored.pricing).toEqual(source.pricing);
  });

  it("разбирает schema4 парк и scalar/matrix ставки в типизированные строки", () => {
    const form = formFromPack(schema4Pack());
    expect(form.process_park[0]).toMatchObject({
      process_code: "CUT.LASER.SHEET",
      rate_id: "svc:laser-sheet",
      supplementary_rate_ids: ["svc:laser-pierce"],
      requires_manual_review: true,
      in_house_axis_max: [{ axis_name: "thickness_mm", maximum: "20" }],
    });
    expect(form.rate_registry[0]).toMatchObject({
      kind: "scalar",
      rate_id: "svc:laser-pierce",
      value: "7",
    });
    expect(form.rate_registry[1]).toMatchObject({
      kind: "matrix",
      rate_id: "svc:laser-sheet",
      axes: [
        { name: "thickness_mm", unit: "мм", edges: ["0", "1", "2"], open_end: true },
      ],
      grid: [["10"], ["20"], [null]],
    });
  });

  it("сохраняет совместимые неизвестные поля schema4 при редактировании", () => {
    const pack = schema4Pack();
    (pack as Record<string, unknown>).future_pack_field = { enabled: true };
    const process = (pack.process_park as Record<string, Record<string, unknown>>)[
      "CUT.LASER.SHEET"
    ];
    process.future_process_field = "keep";
    const rate = (pack.rate_registry as Record<string, Record<string, unknown>>)[
      "svc:laser-sheet"
    ];
    rate.future_rate_field = "keep";
    (rate.source as Record<string, unknown>).future_source_field = "keep";
    const axis = (rate.axes as Record<string, unknown>[])[0];
    axis.future_axis_field = "keep";

    const restored = packFromForm(formFromPack(pack));
    expect(restored.future_pack_field).toEqual({ enabled: true });
    expect(
      (restored.process_park as Record<string, Record<string, unknown>>)[
        "CUT.LASER.SHEET"
      ].future_process_field,
    ).toBe("keep");
    const restoredRate = (restored.rate_registry as Record<string, Record<string, unknown>>)[
      "svc:laser-sheet"
    ];
    expect(restoredRate.future_rate_field).toBe("keep");
    expect((restoredRate.source as Record<string, unknown>).future_source_field).toBe(
      "keep",
    );
    expect((restoredRate.axes as Record<string, unknown>[])[0].future_axis_field).toBe(
      "keep",
    );
  });
});

function schema4Pack(): Record<string, unknown> {
  const pack = packFromForm(filled());
  pack.schema_version = 4;
  pack.process_park = {
    "CUT.LASER.SHEET": {
      rate_id: "svc:laser-sheet",
      supplementary_rate_ids: ["svc:laser-pierce"],
      requires_manual_review: true,
      in_house_axis_max: { thickness_mm: "20" },
      note: "Лазер предприятия",
    },
  };
  pack.rate_registry = {
    "svc:laser-pierce": {
      kind: "scalar",
      tariff_unit: "per_pierce",
      vat_included: false,
      source: { ...MONEY },
      value: "7",
    },
    "svc:laser-sheet": {
      kind: "matrix",
      tariff_unit: "per_cut_m",
      vat_included: false,
      source: { ...MONEY },
      axes: [
        {
          name: "thickness_mm",
          unit: "мм",
          edges: ["0", "1", "2"],
          open_end: true,
        },
      ],
      grid: [["10"], ["20"], [null]],
    },
  };
  (pack.pricing as Record<string, unknown>).material_markup_percent = "15";
  (pack.pricing as Record<string, unknown>).rates_include_vat = true;
  return pack;
}

describe("schema4 validation", () => {
  it("ловит дубли ID и ссылки процесса на отсутствующие ставки", () => {
    const form = formFromPack(schema4Pack());
    form.rate_registry.push({ ...form.rate_registry[0] });
    form.process_park[0].supplementary_rate_ids.push("svc:missing");
    const issues = validateSchema4(form);
    expect(issues.some((issue) => issue.message.includes("ID ставки уже есть"))).toBe(true);
    expect(issues.some((issue) => issue.message.includes("svc:missing"))).toBe(true);
  });

  it("проверяет scalar число, возрастающие edges и точную размерность grid", () => {
    const form = formFromPack(schema4Pack());
    const scalar = form.rate_registry[0];
    if (scalar.kind !== "scalar") throw new Error("fixture");
    scalar.value = "семь";
    const matrix = form.rate_registry[1];
    if (matrix.kind !== "matrix") throw new Error("fixture");
    matrix.axes[0].edges = ["0", "2", "1"];
    matrix.grid = [["10"]];
    const issues = validateSchema4(form);
    expect(issues.some((issue) => issue.message.includes("числом больше нуля"))).toBe(true);
    expect(issues.some((issue) => issue.message.includes("строго возрастать"))).toBe(true);
    expect(issues.some((issue) => issue.message.includes("3 строки"))).toBe(true);
  });

  it("точно считает форму одно- и двумерной матрицы и безопасно меняет сетку", () => {
    const rate: MatrixRateRow = {
      kind: "matrix",
      rate_id: "svc:test",
      tariff_unit: "per_piece",
      vat_included: false,
      source: { ...MONEY },
      axes: [
        { name: "size", unit: "мм", edges: ["0", "10", "20"], open_end: true, extra: {} },
        { name: "grade", unit: "код", edges: ["0", "1", "2"], open_end: false, extra: {} },
      ],
      grid: [["1", "2"]],
      extra: {},
    };
    expect(matrixShape(rate)).toEqual({ rows: 3, columns: 2 });
    expect(resizeMatrixGrid(rate).grid).toEqual([
      ["1", "2"],
      [null, null],
      [null, null],
    ]);
  });

  it("валидная schema4 форма проходит локальную проверку", () => {
    expect(validateSchema4(formFromPack(schema4Pack()))).toEqual([]);
  });
});

describe("источники", () => {
  it("источник неполон, пока не заполнены все три поля", () => {
    expect(isSourceComplete({ kind: "", ref: "прайс", as_of: "2026-08-20" })).toBe(false);
    expect(isSourceComplete({ kind: "client_canon", ref: " ", as_of: "2026-08-20" })).toBe(false);
    expect(isSourceComplete({ kind: "client_canon", ref: "прайс", as_of: "20.08.2026" })).toBe(false);
    expect(isSourceComplete({ kind: "client_canon", ref: "прайс", as_of: "2026-08-20" })).toBe(true);
  });

  it("считает строки без источника — счётчик висит в шапке экрана", () => {
    const form = filled();
    expect(countMissingSources(form)).toBe(0);
    form.materials[0].source = { kind: "", ref: "", as_of: "2026-08-20" };
    expect(countMissingSources(form)).toBe(1);
  });

  it("выключенная строка источника не требует", () => {
    const form = filled();
    form.machines[1].enabled = false;
    expect(countMissingSources(form)).toBe(0);
  });
});

describe("validateForm", () => {
  it("на заполненной форме молчит", () => {
    expect(validateForm(filled())).toEqual([]);
  });

  it("ловит ставку, которая не число", () => {
    const form = filled();
    form.blank_ops[0].rate_rub = "около ста";
    expect(validateForm(form).some((issue) => issue.where.includes("bandsaw"))).toBe(true);
  });

  it("ловит два материала с одним кодом", () => {
    // Ключ материала уезжает в material_code инструмента: дубль означает, что
    // одна из строк молча исчезнет из пака.
    const form = filled();
    form.materials.push({ ...form.materials[0] });
    expect(validateForm(form).some((issue) => issue.message.includes("уже есть"))).toBe(true);
  });

  it("ловит код материала с пробелом", () => {
    const form = filled();
    form.materials[0].code = "steel 40x";
    expect(validateForm(form).some((issue) => issue.message.includes("латиница"))).toBe(true);
  });

  it("ловит режим для группы, которой нет ни у одного материала", () => {
    // Иначе это всплывёт только в бою, на расчёте времени.
    const form = filled();
    form.norms[0].material_group = "titanium";
    expect(validateForm(form).some((issue) => issue.message.includes("такой группы нет"))).toBe(
      true,
    );
  });

  it("требует минимальный набор, без которого считать нечего", () => {
    const form = emptyForm();
    expect(validateForm(form).some((issue) => issue.where === "Минимум")).toBe(true);
  });

  it("не дублирует бизнес-правила движка", () => {
    // НДС 300% — забота валидатора движка. Продублировав правило здесь, мы
    // завели бы второй свод, который однажды разойдётся с первым.
    const form = filled();
    form.pricing.vat_rate_pct = "300";
    expect(validateForm(form)).toEqual([]);
  });
});

describe("canPublish", () => {
  it("называет причину, по которой кнопка выключена", () => {
    const form = filled();
    form.materials[0].source = { kind: "", ref: "", as_of: "2026-08-20" };
    const verdict = canPublish(form);
    expect(verdict.ok).toBe(false);
    expect(verdict.reason).toContain("Строк без источника: 1");
  });

  it("пропускает заполненную форму", () => {
    expect(canPublish(filled()).ok).toBe(true);
  });
});

describe("materialGroups", () => {
  it("подсказывает только те группы, что реально заведены", () => {
    const form = filled();
    form.materials.push({ ...form.materials[0], code: "steel-09g2s", group: " alloy " });
    expect(materialGroups(form)).toEqual(["alloy", "steel"]);
  });
});

describe("isPositiveNumber", () => {
  it("принимает точку и отвергает всё остальное", () => {
    expect(isPositiveNumber("0.3")).toBe(true);
    expect(isPositiveNumber("0")).toBe(false);
    expect(isPositiveNumber("0,3")).toBe(false);
    expect(isPositiveNumber("-1")).toBe(false);
    expect(isPositiveNumber("")).toBe(false);
  });
});
