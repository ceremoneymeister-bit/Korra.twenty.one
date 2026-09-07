/**
 * Правила экрана «Заказы».
 *
 * Здесь проверяется то, что на скриншоте выглядит одинаково при верном и
 * неверном поведении: какую стадию считать текущей, что предложить человеку
 * и в каком порядке приоритетов.
 */

import { describe, expect, it } from "vitest";

import {
  actionDraft,
  contractorQuoteValidationError,
  customerPriceRows,
  customerPriceStatusLabel,
  currentHumanQaGate,
  formatMoney,
  isCustomerPriceFinal,
  manualReviewValidationError,
  nextAction,
  orderStatusLabel,
  qaVerdictLabel,
  qaVerdictValidationError,
  stageStatusLabel,
  unresolvedBlockers,
  type OrderCard,
} from "./calc-orders";

function order(overrides: Partial<OrderCard> = {}): OrderCard {
  return {
    kind: "legacy",
    order_id: "ord-1",
    revision: 1,
    status: "draft",
    created_at: "2026-08-25T09:00:00Z",
    updated_at: "2026-08-25T12:00:00Z",
    customer: "Аргентум",
    stages: {
      blank: { status: "pending", provisional: false },
      route: { status: "pending", provisional: false },
      time: { status: "pending", provisional: false },
      quote: { status: "pending", provisional: false },
    },
    current_stage: "blank",
    provisional: false,
    price: null,
    warnings: [],
    ...overrides,
  };
}

const WORKFLOW_READY: OrderCard = {
  kind: "workflow",
  order_id: "34112-P01",
  revision: 5,
  status: "READY_FOR_LD",
  status_title: "Готово для решения человека",
  created_at: "2026-09-04T06:00:00Z",
  updated_at: "2026-09-04T07:10:00Z",
  customer: "Техстком",
  stages: {},
  current_stage: null,
  provisional: false,
  stale_pack: false,
  book_binding_valid: true,
  contractor_quotes: [],
  price: {
    amount_kind: "CUSTOMER_PRICE",
    net_total_rub: 309294.38,
    vat_amount_rub: 68044.76,
    vat_rate_pct: 22,
    total_rub: 377339.14,
    currency: "RUB",
    valid_until: "2099-09-18",
    is_final: true,
  },
  blockers: [],
  next_action: {
    kind: "human_decision",
    label: "Проверить итог и принять решение по расчёту",
    profile: null,
  },
  qa_receipts: {
    mechanical: {
      title: "Механическая проверка",
      verdict: "PASS",
      status: "complete",
      computed_by: "engine",
      checks_total: 2,
      checks_passed: 2,
    },
    technological: {
      title: "Технологическая проверка",
      verdict: "PASS",
      status: "complete",
      actor_role: "qa",
    },
    commercial: {
      title: "Коммерческая проверка",
      verdict: "PASS",
      status: "complete",
      actor_role: "qa",
    },
  },
  warnings: [],
};

const WORKFLOW_BLOCKED: OrderCard = {
  ...WORKFLOW_READY,
  order_id: "34112-P12",
  status: "QA_TECHNOLOGICAL_PASS",
  status_title: "Технологическая проверка пройдена",
  provisional: true,
  price: { ...WORKFLOW_READY.price!, is_final: false },
  blockers: [
    {
      kind: "route_unpriced",
      title: "Нет цены подрядчика: Порошковая окраска",
      detail: "Ждём КП подрядчика",
    },
    {
      kind: "manual_review",
      title: "Нужна ручная сверка: FORM.BEND.SHEET",
    },
    {
      kind: "qa_pending",
      title: "Ожидается: коммерческая проверка",
    },
  ],
  next_action: {
    kind: "contractor_quote",
    label: "Получить и учесть КП подрядчика",
    profile: null,
  },
  qa_receipts: {
    ...WORKFLOW_READY.qa_receipts,
    commercial: {
      title: "Коммерческая проверка",
      verdict: null,
      status: "pending",
    },
  },
};

describe("nextAction", () => {
  it("для workflow использует записанное следующее действие без legacy-догадок", () => {
    expect(nextAction(WORKFLOW_READY)).toEqual(WORKFLOW_READY.next_action);
    expect(nextAction(WORKFLOW_READY).kind).toBe("human_decision");
  });

  it("непроверенная цена металла бьёт всё остальное", () => {
    // service2 не даст утвердить стадию с provisional-ценой. Предложить здесь
    // «утвердить» значит увести человека в отказ инструмента.
    const card = order({
      current_stage: "route",
      provisional: true,
      stages: {
        blank: { status: "approved", provisional: true },
        route: { status: "proposed", provisional: false },
        time: { status: "pending", provisional: false },
        quote: { status: "pending", provisional: false },
      },
    });
    const action = nextAction(card);
    expect(action.kind).toBe("supply");
    expect(action.stage).toBe("blank");
  });

  it("предложенную стадию отправляет на проверку", () => {
    const card = order({
      current_stage: "route",
      stages: {
        blank: { status: "approved", provisional: false },
        route: { status: "proposed", provisional: false },
        time: { status: "pending", provisional: false },
        quote: { status: "pending", provisional: false },
      },
    });
    const action = nextAction(card);
    expect(action.kind).toBe("approve");
    expect(action.stage).toBe("route");
    expect(action.label).toContain("Маршрут");
  });

  it("не начатую стадию предлагает запустить", () => {
    const action = nextAction(order());
    expect(action.kind).toBe("run");
    expect(action.stage).toBe("blank");
  });

  it("пройденный конвейер ничего не требует", () => {
    const action = nextAction(order({ current_stage: null }));
    expect(action.kind).toBe("done");
    expect(action.stage).toBeNull();
  });

  it("устаревшие данные бьют всё: любое другое действие ведёт в отказ", () => {
    // Даже «подтвердить цену металла» бессмысленно, пока стадии посчитаны по
    // прежней ревизии данных — движок откажет, и человек упрётся.
    const card = order({
      current_stage: "route",
      provisional: true,
      stale_stages: ["blank", "route"],
      stages: {
        blank: { status: "approved", provisional: true },
        route: { status: "proposed", provisional: false },
        time: { status: "pending", provisional: false },
        quote: { status: "pending", provisional: false },
      },
    });
    const action = nextAction(card);
    expect(action.kind).toBe("repack");
    expect(action.stage).toBe("blank");
    expect(action.label).toContain("Пересчитать");
    expect(actionDraft(card)).toContain("pipeline_repack");
  });
});

describe("workflow price and blockers", () => {
  it("labels sidebar money as final only after every finality guard", () => {
    const today = new Date("2026-09-04T12:00:00.000Z");
    expect(customerPriceStatusLabel(WORKFLOW_READY, today)).toBe("Окончательная");
    expect(customerPriceStatusLabel(WORKFLOW_BLOCKED, today)).toBe("Предварительная");
    expect(
      customerPriceStatusLabel({ ...WORKFLOW_READY, book_binding_valid: false }, today),
    ).toBe("Предварительная");
    expect(
      customerPriceStatusLabel(
        {
          ...WORKFLOW_READY,
          contractor_quotes: [
            {
              seq: 4,
              process_code: "FINISH.POWDER_COAT",
              status: "stale",
              stale_reasons: ["valid_until"],
            },
          ],
        },
        today,
      ),
    ).toBe("Предварительная");
  });

  it("показывает READY_FOR_LD цену заказчику: нетто, НДС и итог", () => {
    expect(customerPriceRows(WORKFLOW_READY.price)).toEqual([
      { key: "net", label: "Без НДС", value: "309 294,38 ₽" },
      { key: "vat", label: "НДС 22%", value: "68 044,76 ₽" },
      {
        key: "gross",
        label: "Итого заказчику",
        value: "377 339,14 ₽",
        emphasis: true,
      },
    ]);
  });

  it("не прячет незакрытые цену подрядчика, ручную сверку и QA-гейт", () => {
    expect(unresolvedBlockers(WORKFLOW_BLOCKED).map((item) => item.kind)).toEqual([
      "route_unpriced",
      "manual_review",
      "qa_pending",
    ]);
    expect(nextAction(WORKFLOW_BLOCKED).kind).toBe("contractor_quote");
    expect(qaVerdictLabel(WORKFLOW_BLOCKED.qa_receipts!.commercial)).toBe(
      "Ожидается",
    );
  });

  it("считает цену окончательной только после свежих валидных QA-квитанций", () => {
    expect(isCustomerPriceFinal(WORKFLOW_READY)).toBe(true);

    const withoutQa: OrderCard = {
      ...WORKFLOW_READY,
      status: "BOOK_ASSEMBLED",
      qa_receipts: {},
    };
    expect(isCustomerPriceFinal(withoutQa)).toBe(false);

    const stale: OrderCard = { ...WORKFLOW_READY, stale_pack: true };
    expect(isCustomerPriceFinal(stale)).toBe(false);

    const forgedMechanical: OrderCard = {
      ...WORKFLOW_READY,
      qa_receipts: {
        ...WORKFLOW_READY.qa_receipts,
        mechanical: {
          ...WORKFLOW_READY.qa_receipts!.mechanical,
          computed_by: "operator",
          status: "attention",
        },
      },
    };
    expect(isCustomerPriceFinal(forgedMechanical)).toBe(false);
    expect(qaVerdictLabel(forgedMechanical.qa_receipts!.mechanical)).toBe(
      "Недействительно",
    );

    expect(
      isCustomerPriceFinal({ ...WORKFLOW_READY, book_binding_valid: false }),
    ).toBe(false);

    const forgedHuman: OrderCard = {
      ...WORKFLOW_READY,
      qa_receipts: {
        ...WORKFLOW_READY.qa_receipts,
        technological: {
          ...WORKFLOW_READY.qa_receipts!.technological,
          actor_role: "front",
          status: "complete",
        },
      },
    };
    expect(isCustomerPriceFinal(forgedHuman)).toBe(false);

    const today = new Date("2026-09-04T23:59:59Z");
    expect(
      isCustomerPriceFinal({
        ...WORKFLOW_READY,
        price: { ...WORKFLOW_READY.price!, valid_until: "2026-09-04" },
      }, today),
    ).toBe(true);
    for (const valid_until of ["2026-09-03", "завтра", "2026-02-29"]) {
      expect(
        isCustomerPriceFinal({
          ...WORKFLOW_READY,
          price: { ...WORKFLOW_READY.price!, valid_until },
        }, today),
      ).toBe(false);
    }
  });
});

describe("human QA actions", () => {
  const waitingForTech: OrderCard = {
    ...WORKFLOW_READY,
    status: "QA_MECHANICAL_PASS",
    revision: 17,
    next_action: {
      kind: "qa_human",
      label: "Провести технологическую проверку человеком",
      profile: null,
      qa_gate: "technological",
    },
    qa_receipts: {
      mechanical: WORKFLOW_READY.qa_receipts!.mechanical,
      technological: {
        title: "Технологическая проверка",
        verdict: null,
        status: "pending",
      },
      commercial: {
        title: "Коммерческая проверка",
        verdict: null,
        status: "pending",
      },
    },
  };

  it("разрешает форму только для текущего human-auth гейта", () => {
    expect(currentHumanQaGate(waitingForTech)).toBe("technological");
    expect(
      currentHumanQaGate({ ...waitingForTech, book_binding_valid: false }),
    ).toBeNull();
    expect(
      currentHumanQaGate({
        ...waitingForTech,
        next_action: {
          kind: "qa_engine",
          label: "Ожидается автоматическая механическая проверка",
          qa_gate: "mechanical",
        },
      }),
    ).toBeNull();

    expect(
      currentHumanQaGate({
        ...waitingForTech,
        status: "QA_TECHNOLOGICAL_PASS",
        next_action: {
          kind: "qa_human",
          label: "Провести коммерческую проверку человеком",
          qa_gate: "commercial",
        },
        qa_receipts: {
          ...waitingForTech.qa_receipts,
          technological: {
            title: "Технологическая проверка",
            verdict: "PASS",
            status: "complete",
            actor_role: "front",
          },
        },
      }),
    ).toBeNull();
  });

  it("требует причину для каждого решения кроме PASS", () => {
    expect(qaVerdictValidationError("PASS", "")).toBeNull();
    for (const verdict of ["ADJUST", "BLOCK", "NO_EVIDENCE"] as const) {
      expect(qaVerdictValidationError(verdict, "")).toContain("причину");
    }
    expect(qaVerdictValidationError("BLOCK", "Проверить допуск")).toBeNull();
    expect(qaVerdictValidationError("NO_EVIDENCE", "Нет чертежа")).toBeNull();
    expect(qaVerdictValidationError("ADJUST", "Проверить допуск")).toContain(
      "исполнителя",
    );
    expect(
      qaVerdictValidationError("ADJUST", "Проверить допуск", "norm"),
    ).toBeNull();
    expect(qaVerdictValidationError("PASS", "", "front")).toContain(
      "только для корректировки",
    );
  });
});

describe("contractor quote form", () => {
  const quote = {
    amount_rub: "1250.50",
    basis: "order_total" as const,
    vat_included: true,
    quoted_at: "2026-09-04",
    valid_until: "2026-10-04",
    source: "ООО Покраска",
    reference: "КП-42",
  };

  it("requires a positive typed RUB amount, date, source and reference", () => {
    expect(contractorQuoteValidationError(quote)).toBeNull();
    expect(contractorQuoteValidationError({ ...quote, amount_rub: "0" })).toContain(
      "больше нуля",
    );
    expect(
      contractorQuoteValidationError({ ...quote, quoted_at: "2026-02-29" }),
    ).toContain("корректную дату");
    expect(
      contractorQuoteValidationError({ ...quote, valid_until: "2026-09-03" }),
    ).toContain("раньше даты");
    expect(contractorQuoteValidationError({ ...quote, source: " " })).toContain(
      "источник",
    );
    expect(contractorQuoteValidationError({ ...quote, reference: "" })).toContain(
      "номер",
    );
  });
});

describe("manual review receipt", () => {
  const items = [
    {
      item_index: 0,
      item_digest: "a".repeat(64),
      seq: 2,
      process_code: "FORM.BEND.SHEET",
      reason: "Сверить правило",
    },
  ];
  const drafts = [
    {
      item_index: 0,
      item_digest: "a".repeat(64),
      evidence: "Ставка и формула сверены",
      reference: "ТК-42",
      confirmed: true,
    },
  ];

  it("requires reviewer, per-item evidence/reference and explicit confirmation", () => {
    expect(manualReviewValidationError(items, drafts, "Лариса")).toBeNull();
    expect(manualReviewValidationError(items, drafts, " ")).toContain("кто");
    expect(
      manualReviewValidationError(items, [{ ...drafts[0], evidence: "" }], "Лариса"),
    ).toContain("результат");
    expect(
      manualReviewValidationError(items, [{ ...drafts[0], reference: "" }], "Лариса"),
    ).toContain("документ");
    expect(
      manualReviewValidationError(
        items,
        [{ ...drafts[0], confirmed: false }],
        "Лариса",
      ),
    ).toContain("подтвердите");
  });

  it("keeps a manual-review price preliminary until a valid exact receipt exists", () => {
    const withManual = {
      ...WORKFLOW_READY,
      manual_review_required: items,
      manual_review_receipt: null,
    };
    expect(isCustomerPriceFinal(withManual)).toBe(false);
    expect(
      isCustomerPriceFinal({
        ...withManual,
        manual_review_receipt: { valid: true },
      }),
    ).toBe(true);
  });
});

describe("actionDraft", () => {
  it("для звёздочки зовёт именно supply_confirm", () => {
    const card = order({
      current_stage: "route",
      provisional: true,
      stages: {
        blank: { status: "approved", provisional: true },
        route: { status: "proposed", provisional: false },
        time: { status: "pending", provisional: false },
        quote: { status: "pending", provisional: false },
      },
    });
    const draft = actionDraft(card);
    expect(draft).toContain("supply_confirm");
    expect(draft).toContain("ord-1");
  });

  it("для проверки зовёт pipeline_status и stage_approve", () => {
    const card = order({
      current_stage: "time",
      stages: {
        blank: { status: "approved", provisional: false },
        route: { status: "approved", provisional: false },
        time: { status: "proposed", provisional: false },
        quote: { status: "pending", provisional: false },
      },
    });
    const draft = actionDraft(card);
    expect(draft).toContain("pipeline_status");
    expect(draft).toContain("stage_approve");
    expect(draft).toContain("Нормы времени");
  });
});

describe("formatMoney", () => {
  it("показывает копейки — округление на экране прячет расхождение", () => {
    // Разделитель разрядов у Intl — неразрывный пробел; сравниваем по цифрам.
    const rendered = formatMoney(11618.4).replace(/\s/g, " ");
    expect(rendered).toBe("11 618,40 ₽");
  });

  it("пустое значение не превращает в ноль", () => {
    // «0,00 ₽» и «нет цифры» — разные утверждения; второе честнее.
    expect(formatMoney(undefined)).toBe("—");
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney(Number.NaN)).toBe("—");
  });
});

describe("stageStatusLabel", () => {
  it("переводит состояния на язык расчётчика", () => {
    expect(stageStatusLabel("approved")).toBe("утверждена");
    expect(stageStatusLabel("proposed")).toBe("на проверке");
    expect(stageStatusLabel(undefined)).toBe("не начата");
  });

  it("незнакомое состояние показывает как есть, а не прячет", () => {
    expect(stageStatusLabel("rejected")).toBe("rejected");
  });
});

describe("orderStatusLabel", () => {
  it("переводит состояния реестра на язык человека", () => {
    expect(orderStatusLabel("draft")).toBe("черновик");
    expect(orderStatusLabel("needs_human")).toBe("нужен человек");
    expect(orderStatusLabel("quoted")).toBe("КП отправлено");
  });

  it("незнакомое состояние показывает как есть, а не выдумывает", () => {
    expect(orderStatusLabel("frobnicated")).toBe("frobnicated");
    expect(orderStatusLabel(undefined)).toBe("—");
  });
});
