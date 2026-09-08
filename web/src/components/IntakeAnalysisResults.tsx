import type { AnalysisProposal, AnalysisSource, ProposedFact, ProposedQuantity } from "@/lib/calc-intake-analysis";
import { IntakeAnalysisSourceAccess } from "./IntakeAnalysisSourceAccess";

const roles = { make: "Изготовление", buy: "Покупное", customer_supplied: "Давальческое", excluded: "Исключено из расчёта", unknown: "Роль уточняется" };
const blockers = { composition_acceptance: "Для подтверждения состава", calculation: "Для расчёта", quote: "Для КП", none: "Уточнение" };
const sourceStatuses: Record<string, string> = {
  pending: "Ожидает чтения", reading: "Чтение документа", rendering: "Подготовка страниц", ready: "Ожидает разбора", running: "Разбор документа", complete: "Предложение сохранено", failed: "Ошибка обработки", unsupported: "Формат требует проверки", blocked: "Нужна проверка",
};
const fieldNames: Record<string, string> = {
  designation: "Обозначение", name: "Наименование", title: "Наименование", material: "Материал", material_grade: "Марка материала", material_standard: "Стандарт материала", grade: "Марка", thickness: "Толщина", length: "Длина", width: "Ширина", diameter: "Диаметр", mass: "Масса", weight: "Масса", coating: "Покрытие", welding: "Сварка", weld: "Сварка", requirements: "Требования", requirement: "Требование", tolerance: "Допуск", finish: "Обработка поверхности", quantity: "Количество", variant: "Исполнение",
};

function value(fact: ProposedFact): string {
  const raw = fact.normalized_value ?? fact.raw_text;
  if (raw === null) return fact.unknown_reason || "Нужно уточнить";
  return `${typeof raw === "boolean" ? (raw ? "Да" : "Нет") : raw}${fact.unit ? ` ${fact.unit}` : ""}`;
}

function evidence(proposal: AnalysisProposal, ids: string[]): string {
  return ids.map((id) => proposal.evidence.find((entry) => entry.evidence_id === id)?.locator).filter((entry) => entry !== undefined).map((locator) => {
    if (locator.kind === "pdf") return `стр. ${locator.page}`;
    if (locator.kind === "xlsx") return `${locator.sheet}, ${locator.cell}`;
    return `${locator.layout}, элемент ${locator.entity_handle}`;
  }).join("; ");
}

function Quantity({ quantity, proposal }: { quantity: ProposedQuantity; proposal: AnalysisProposal }) {
  return <>{quantity.value === null ? "Нужно уточнить" : `${quantity.value}${quantity.unit ? ` ${quantity.unit}` : ""}`}
    {quantity.evidence_ids.length > 0 && <span className="block text-xs text-text-secondary">{evidence(proposal, quantity.evidence_ids)}</span>}
  </>;
}

function positionName(proposal: AnalysisProposal, productId: string): string {
  const position = proposal.positions.find((entry) => entry.product_id === productId || entry.position_id === productId);
  const fact = proposal.facts.find((entry) => entry.fact_id === position?.designation_fact_id);
  return fact ? value(fact) : "Позиция документа";
}

export function IntakeAnalysisResults({ rows }: { rows: AnalysisSource[] }) {
  return <div className="space-y-3">
    {rows.map((row) => <details key={row.source_id} open={Boolean(row.proposal)} className="rounded-lg border border-border">
      <summary className="cursor-pointer break-words px-3 py-2 font-medium">{row.relative_path}<span className="ml-2 font-normal text-text-secondary">{sourceStatuses[row.status] ?? "Проверка состояния"}</span></summary>
      <IntakeAnalysisSourceAccess source={row} />
      {row.proposal && <div className="space-y-3 px-3 pb-3">
        <p className="text-xs text-text-secondary">Предложения из этого источника. Совпадающие позиции разных документов ещё требуют сопоставления и подтверждения сотрудником.</p>
        {row.proposal.positions.length > 0 && <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <thead><tr className="border-b border-border"><th className="p-2">Позиция</th><th className="p-2">Роль</th><th className="p-2">Количество к изготовлению / поставке</th></tr></thead>
          <tbody>{row.proposal.positions.map((position, index) => {
            const designation = row.proposal!.facts.find((fact) => fact.fact_id === position.designation_fact_id);
            const variant = row.proposal!.facts.find((fact) => fact.fact_id === position.variant_fact_id);
            return <tr key={position.position_id} className="border-b border-border align-top">
              <td className="p-2">{designation ? value(designation) : `Позиция ${index + 1}`}
                {variant && <span className="block">Исполнение: {value(variant)}</span>}
                {designation && <span className="block text-xs text-text-secondary">{evidence(row.proposal!, designation.evidence_ids)}</span>}
              </td>
              <td className="p-2">{roles[position.role]}</td>
              <td className="p-2"><Quantity quantity={position.quantity} proposal={row.proposal!} /></td>
            </tr>;
          })}</tbody>
        </table></div>}
        {row.proposal.facts.length > 0 && <div className="space-y-2">
          <p className="font-medium">Материалы, требования и другие сведения</p>
          <dl className="space-y-2">{row.proposal.facts.map((fact) => <div key={fact.fact_id} className="break-words">
            <dt className="text-xs text-text-secondary">{positionName(row.proposal!, fact.subject_id)} · {fieldNames[fact.field_key] ?? "Сведение документа"}</dt>
            <dd>{value(fact)}{fact.status !== "extracted" && <span className="ml-2 text-xs text-warning">{fact.status === "conflicting" ? "Есть расхождение" : "Требует проверки"}</span>}
              <span className="block text-xs text-text-secondary">{evidence(row.proposal!, fact.evidence_ids)}</span>
            </dd>
          </div>)}</dl>
        </div>}
        {row.proposal.relations.some((relation) => relation.quantity_per_parent) && <div className="space-y-2">
          <p className="font-medium">Компоненты сборок</p>
          {row.proposal.relations.filter((relation) => relation.quantity_per_parent).map((relation) => <p key={relation.relation_id}>
            {positionName(row.proposal!, relation.from.id)} → {positionName(row.proposal!, relation.to.id)}: на одну сборку <Quantity quantity={relation.quantity_per_parent!} proposal={row.proposal!} />
          </p>)}
        </div>}
        {row.proposal.issues.length > 0 && <div className="space-y-2">
          <p className="font-medium">Вопросы по источнику</p>
          <ul className="list-disc space-y-2 pl-5">{row.proposal.issues.map((issue, index) => <li key={issue.issue_id ?? `${issue.code}-${index}`}><span className="text-xs text-text-secondary">{blockers[issue.blocks]}: </span>{issue.question}</li>)}</ul>
        </div>}
      </div>}
    </details>)}
  </div>;
}
