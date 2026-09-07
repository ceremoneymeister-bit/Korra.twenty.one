/**
 * CalcDataPage — данные предприятия.
 *
 * Экран, ради которого расчётчик перестаёт зависеть от нас. Прайсы, режимы
 * резания и политика цены до сих пор попадали в контур только файлом с нашей
 * стороны — из-за этого каталог ставок у клиента стоял пустой, а три агента
 * не могли посчитать ни одного заказа. Здесь методолог заводит их сама и
 * правит в любой момент: паки не кэшируются, и следующий же вызов
 * инструмента считает по новым цифрам.
 *
 * Два правила экрана, которые важнее вёрстки:
 *
 * 1. **Источник обязателен у каждой цифры.** Он не поле в конце строки, а
 *    её часть; строка без источника подсвечена, счётчик таких строк висит в
 *    шапке, и кнопка публикации выключена, пока счётчик не ноль — с прямо
 *    написанной причиной, а не молча.
 * 2. **Судья — сервер.** Здесь проверяется только форма (пусто / не число /
 *    нет даты). Содержательные правила остаются в движке, и «Опубликовать»
 *    всегда проходит через его валидатор. Второй свод правил в браузере
 *    однажды разошёлся бы с движком — в деньгах и молча.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, Plus, RotateCcw, Trash2 } from "lucide-react";

import { ProductButton } from "@/components/ProductButton";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { Input } from "@nous-research/ui/ui/components/input";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Switch } from "@nous-research/ui/ui/components/switch";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { fetchJSON } from "@/lib/api";
import { flushDraftBeforeAction } from "@/lib/calc-rates-draft";
import { cn } from "@/lib/utils";
import {
  axisTitle,
  availableRateIds,
  BLANK_OPS,
  EXTRA_CODES,
  MACHINE_OPS,
  MARGIN_BASIS,
  MONEY_SOURCE_KINDS,
  NORM_FIELDS,
  NORM_SOURCE_KINDS,
  PROCESS_TITLES,
  RATE_KINDS,
  ROUNDING,
  STOCK_KINDS,
  TARIFF_UNITS,
  emptyForm,
  emptySource,
  formFromPack,
  formatMoment,
  materialGroups,
  matrixShape,
  newMatrixRate,
  newProcessParkRow,
  newScalarRate,
  packFromForm,
  processTitle,
  rateTitle,
  resizeMatrixGrid,
  today,
  validateSchema4,
  type ActiveRatesResponse,
  type MatrixAxisRow,
  type MatrixRateRow,
  type NormRow,
  type ProcessParkRow,
  type RateRegistryRow,
  type RatesForm,
  type RevisionsResponse,
  type Schema4Issue,
  type SourceForm,
} from "@/lib/calc-rates";
import { canPublish, isSourceComplete } from "@/lib/calc-rates-validate";

/* ------------------------------------------------------------------ */
/*  Мелкие части                                                       */
/* ------------------------------------------------------------------ */

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3 border-t border-border pt-5">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold">{title}</h2>
        {hint && (
          <p className="max-w-[70ch] text-sm leading-relaxed text-text-secondary">
            {hint}
          </p>
        )}
      </header>
      {children}
    </section>
  );
}

function TextField({
  label,
  value,
  onChange,
  placeholder,
  list,
  className,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  list?: string;
  className?: string;
}) {
  return (
    <label className={cn("flex min-w-0 flex-col gap-1 text-sm", className)}>
      <span className="text-text-secondary">{label}</span>
      <Input
        value={value}
        placeholder={placeholder}
        list={list}
        onChange={(event) => onChange(event.target.value)}
        className="w-full text-base sm:text-sm"
      />
    </label>
  );
}

function SelectField({
  label,
  value,
  onChange,
  children,
  className,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("flex min-w-0 flex-col gap-1 text-sm", className)}>
      <span className="text-text-secondary">{label}</span>
      <Select value={value} onValueChange={onChange} aria-label={label}>
        {children}
      </Select>
    </label>
  );
}

/**
 * Источник цифры: вид, ссылка, дата.
 *
 * Живёт прямо под ценой, а не в отдельной вкладке «метаданные»: увиденное
 * рядом заполняется, увиденное отдельно — откладывается.
 */
function SourceFields({
  source,
  kinds,
  refHint,
  onChange,
}: {
  source: SourceForm;
  kinds: readonly { value: string; title: string }[];
  refHint: string;
  onChange: (next: SourceForm) => void;
}) {
  return (
    <fieldset className="grid min-w-0 gap-2 sm:grid-cols-[minmax(9rem,0.7fr)_minmax(14rem,2fr)_10rem]">
      <legend className="mb-1 text-sm font-medium">Источник ставки</legend>
      <SelectField
        label="Вид источника"
        value={source.kind}
        onChange={(value) => onChange({ ...source, kind: value })}
      >
        <SelectOption value="">Выберите вид</SelectOption>
        {kinds.map((kind) => (
          <SelectOption key={kind.value} value={kind.value}>
            {kind.title}
          </SelectOption>
        ))}
      </SelectField>
      <TextField
        label="Документ или ссылка"
        value={source.ref}
        placeholder={refHint}
        onChange={(value) => onChange({ ...source, ref: value })}
      />
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-text-secondary">Дата источника</span>
        <Input
          type="date"
          value={source.as_of}
          onChange={(event) => onChange({ ...source, as_of: event.target.value })}
          className="w-full text-base sm:text-sm"
        />
      </label>
    </fieldset>
  );
}

/** Строка таблицы: подсветка и бейдж, когда источник не дозаполнен. */
function Row({
  incomplete,
  children,
}: {
  incomplete: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "space-y-1.5 rounded-sm border p-2.5",
        incomplete ? "border-warning/60" : "border-border",
      )}
    >
      {children}
      {/* Не библиотечный Badge: он разряжает надпись на 0,2 эма, и
          «н е т   и с т о ч н и к а» приходится складывать по буквам. */}
      {incomplete && (
        <span className="inline-block rounded-lg border border-warning/60 px-2.5 py-1 text-sm text-warning">
          нет источника
        </span>
      )}
    </div>
  );
}

function NumberInput({
  value,
  onChange,
  label,
  unit,
  className,
}: {
  value: string;
  onChange: (next: string) => void;
  label: string;
  unit?: string;
  className?: string;
}) {
  return (
    <label className={cn("flex items-center gap-2 text-sm", className)}>
      <span className="whitespace-nowrap text-text-secondary">{label}</span>
      <Input
        // type=number ловит половину диапазонов силами браузера, а inputMode
        // decimal даёт на телефоне правильную клавиатуру.
        type="number"
        inputMode="decimal"
        min="0"
        step="any"
        value={value}
        aria-label={label}
        onChange={(event) => onChange(event.target.value)}
        className="w-28 text-base sm:text-sm"
      />
      {unit && (
        <span className="whitespace-nowrap text-text-secondary">{unit}</span>
      )}
    </label>
  );
}

function InlineIssues({ issues }: { issues: Schema4Issue[] }) {
  if (!issues.length) return null;
  return (
    <ul className="space-y-1 text-sm text-warning" role="list">
      {issues.map((issue, index) => (
        <li key={`${issue.where}-${index}`} className="flex items-start gap-1.5">
          <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          {issue.message}
        </li>
      ))}
    </ul>
  );
}

function intervalLabels(axis: MatrixAxisRow | undefined): string[] {
  if (!axis || axis.edges.length < 2) return [];
  const labels = axis.edges.slice(1).map((edge, index) => {
    const previous = axis.edges[index];
    return `(${previous}; ${edge}]`;
  });
  if (axis.open_end) labels.push(`>${axis.edges.at(-1)}`);
  return labels;
}

function MatrixEditor({
  rate,
  onChange,
}: {
  rate: MatrixRateRow;
  onChange: (next: MatrixRateRow) => void;
}) {
  const updateAxis = (index: number, next: MatrixAxisRow) => {
    const axes = [...rate.axes];
    axes[index] = next;
    onChange(resizeMatrixGrid({ ...rate, axes }));
  };
  const rowLabels = intervalLabels(rate.axes[0]);
  const columnLabels = rate.axes.length > 1 ? intervalLabels(rate.axes[1]) : ["Ставка"];

  return (
    <details className="rounded-xl border border-border p-3">
      <summary className="cursor-pointer text-sm font-medium">
        Матрица · {matrixShape(rate).rows} × {matrixShape(rate).columns}
      </summary>
      <div className="mt-4 space-y-4">
        <div className="grid gap-3 xl:grid-cols-2">
          {rate.axes.map((axis, axisIndex) => (
            <fieldset key={axisIndex} className="space-y-3 rounded-xl border border-border p-3">
              <legend className="px-1 text-sm font-medium">Ось {axisIndex + 1}</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                <TextField
                  label="Ключ оси"
                  value={axis.name}
                  placeholder="thickness_mm"
                  onChange={(name) => updateAxis(axisIndex, { ...axis, name })}
                />
                <TextField
                  label="Единица"
                  value={axis.unit}
                  placeholder="мм"
                  onChange={(unit) => updateAxis(axisIndex, { ...axis, unit })}
                />
              </div>
              <p className="text-sm text-text-secondary">
                Название: {axisTitle(axis.name)}
              </p>
              <div className="space-y-2">
                <p className="text-sm font-medium">Границы интервалов</p>
                <div className="flex flex-wrap gap-2">
                  {axis.edges.map((edge, edgeIndex) => (
                    <label key={edgeIndex} className="flex items-end gap-1 text-sm">
                      <span className="flex flex-col gap-1">
                        <span className="text-text-secondary">Граница {edgeIndex + 1}</span>
                        <Input
                          type="number"
                          inputMode="decimal"
                          step="any"
                          value={edge}
                          onChange={(event) => {
                            const edges = [...axis.edges];
                            edges[edgeIndex] = event.target.value;
                            updateAxis(axisIndex, { ...axis, edges });
                          }}
                          className="w-28 text-base sm:text-sm"
                        />
                      </span>
                      <ProductButton
                        ghost
                        size="icon"
                        disabled={axis.edges.length <= 2}
                        aria-label={`Удалить границу ${edgeIndex + 1}`}
                        onClick={() => {
                          if (!window.confirm("Удалить границу и изменить размер сетки?")) return;
                          updateAxis(axisIndex, {
                            ...axis,
                            edges: axis.edges.filter((_, index) => index !== edgeIndex),
                          });
                        }}
                      >
                        <Trash2 />
                      </ProductButton>
                    </label>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <ProductButton
                    outlined
                    size="sm"
                    disabled={axis.edges.length >= 32}
                    onClick={() => {
                      const last = Number(axis.edges.at(-1));
                      const next = Number.isFinite(last) ? String(last + 1) : "";
                      updateAxis(axisIndex, { ...axis, edges: [...axis.edges, next] });
                    }}
                  >
                    <Plus /> Граница
                  </ProductButton>
                  <label className="flex items-center gap-2 text-sm">
                    <Switch
                      checked={axis.open_end}
                      onCheckedChange={(open_end) =>
                        updateAxis(axisIndex, { ...axis, open_end })
                      }
                    />
                    Последний интервал открыт вверх
                  </label>
                  {rate.axes.length > 1 && (
                    <ProductButton
                      ghost
                      destructive
                      size="sm"
                      onClick={() => {
                        if (!window.confirm("Удалить ось и связанные столбцы сетки?")) return;
                        onChange(
                          resizeMatrixGrid({
                            ...rate,
                            axes: rate.axes.filter((_, index) => index !== axisIndex),
                          }),
                        );
                      }}
                    >
                      <Trash2 /> Удалить ось
                    </ProductButton>
                  )}
                </div>
              </div>
            </fieldset>
          ))}
        </div>
        {rate.axes.length < 2 && (
          <ProductButton
            outlined
            size="sm"
            onClick={() =>
              onChange(
                resizeMatrixGrid({
                  ...rate,
                  axes: [
                    ...rate.axes,
                    { name: "", unit: "", edges: ["0", "1"], open_end: false, extra: {} },
                  ],
                }),
              )
            }
          >
            <Plus /> Добавить вторую ось
          </ProductButton>
        )}
        <div className="space-y-2">
          <p className="text-sm font-medium">Сетка ставок</p>
          <p className="text-sm leading-relaxed text-text-secondary">
            Пустая ячейка означает, что для этого диапазона ставки нет.
          </p>
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="min-w-max text-sm">
              <thead>
                <tr className="border-b border-border text-text-secondary">
                  <th className="p-2 text-left font-normal">
                    {axisTitle(rate.axes[0]?.name ?? "")}
                  </th>
                  {columnLabels.map((label, index) => (
                    <th key={index} className="p-2 text-left font-normal">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rate.grid.map((gridRow, rowIndex) => (
                  <tr key={rowIndex} className="border-b border-border/50 last:border-0">
                    <th className="p-2 text-left font-normal text-text-secondary">
                      {rowLabels[rowIndex] ?? `Строка ${rowIndex + 1}`}
                    </th>
                    {gridRow.map((cell, columnIndex) => (
                      <td key={columnIndex} className="p-1.5">
                        <Input
                          type="number"
                          inputMode="decimal"
                          min="0"
                          step="any"
                          value={cell ?? ""}
                          aria-label={`Ставка ${rowLabels[rowIndex] ?? rowIndex + 1}, ${columnLabels[columnIndex] ?? columnIndex + 1}`}
                          onChange={(event) => {
                            const grid = rate.grid.map((item) => [...item]);
                            grid[rowIndex][columnIndex] = event.target.value || null;
                            onChange({ ...rate, grid });
                          }}
                          className="w-24 text-base tabular-nums sm:text-sm"
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </details>
  );
}

function RateEditor({
  rate,
  displayName,
  issues,
  onChange,
  onRemove,
}: {
  rate: RateRegistryRow;
  displayName: string;
  issues: Schema4Issue[];
  onChange: (next: RateRegistryRow) => void;
  onRemove: () => void;
}) {
  const common = {
    rate_id: rate.rate_id,
    tariff_unit: rate.tariff_unit,
    vat_included: rate.vat_included,
    source: rate.source,
    extra: rate.extra,
  };
  return (
    <details
      className="rounded-xl border border-border p-3 sm:p-4"
    >
      <summary className="cursor-pointer">
        <div>
          <h3 className="font-semibold">{displayName}</h3>
          <p className="text-sm text-text-secondary">
            {rate.rate_id || "ID ещё не указан"} ·{" "}
            {rate.kind === "scalar" ? "одно значение" : "значения по диапазонам"}
            {issues.length > 0 ? ` · ошибок: ${issues.length}` : ""}
          </p>
        </div>
      </summary>
      <div className="mt-4 space-y-4">
        <div className="flex justify-end">
          <ProductButton
            ghost
            destructive
            size="sm"
            onClick={() => {
              if (window.confirm(`Удалить ставку «${rate.rate_id || "без ID"}»?`)) onRemove();
            }}
          >
            <Trash2 /> Удалить ставку
          </ProductButton>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <TextField
            label="ID ставки"
            value={rate.rate_id}
            placeholder="svc:laser-sheet"
            onChange={(rate_id) => onChange({ ...rate, rate_id })}
          />
          <SelectField
            label="Тип ставки"
            value={rate.kind}
            onChange={(kind) =>
              kind === "scalar"
                ? onChange({ ...newScalarRate(), ...common, kind: "scalar" })
                : onChange({ ...newMatrixRate(), ...common, kind: "matrix" })
            }
          >
            <SelectOption value="scalar">Одно значение</SelectOption>
            <SelectOption value="matrix">Матрица</SelectOption>
          </SelectField>
          <SelectField
            label="Единица тарифа"
            value={rate.tariff_unit}
            onChange={(tariff_unit) => onChange({ ...rate, tariff_unit })}
          >
            {TARIFF_UNITS.map((unit) => (
              <SelectOption key={unit} value={unit}>
                {unit}
              </SelectOption>
            ))}
          </SelectField>
          {rate.kind === "scalar" && (
            <NumberInput
              label="Значение"
              unit="₽"
              value={rate.value}
              onChange={(value) => onChange({ ...rate, value })}
              className="items-end"
            />
          )}
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Switch
            checked={rate.vat_included}
            onCheckedChange={(vat_included) => onChange({ ...rate, vat_included })}
          />
          Ставка уже включает НДС
        </label>
        {rate.kind === "matrix" && <MatrixEditor rate={rate} onChange={onChange} />}
        <SourceFields
          source={rate.source}
          kinds={MONEY_SOURCE_KINDS}
          refHint="прайс, лист и диапазон ячеек"
          onChange={(source) => onChange({ ...rate, source })}
        />
        <InlineIssues issues={issues} />
      </div>
    </details>
  );
}

function ProcessEditor({
  process,
  editorId,
  primaryRate,
  issues,
  onChange,
  onRemove,
}: {
  process: ProcessParkRow;
  editorId: string;
  primaryRate?: RateRegistryRow;
  issues: Schema4Issue[];
  onChange: (next: ProcessParkRow) => void;
  onRemove: () => void;
}) {
  const primaryAxes = primaryRate?.kind === "matrix" ? primaryRate.axes : [];
  return (
    <details
      className="rounded-xl border border-border p-3 sm:p-4"
    >
      <summary className="cursor-pointer">
        <div>
          <h3 className="font-semibold">{processTitle(process.process_code)}</h3>
          <p className="text-sm text-text-secondary">
            {process.process_code || "Выберите process_code"}
            {process.requires_manual_review ? " · ручная проверка" : ""}
            {issues.length > 0 ? ` · ошибок: ${issues.length}` : ""}
          </p>
        </div>
      </summary>
      <div className="mt-4 space-y-4">
        <div className="flex justify-end">
          <ProductButton
            ghost
            destructive
            size="sm"
            onClick={() => {
              if (window.confirm(`Удалить процесс «${process.process_code || "без кода"}»?`)) {
                onRemove();
              }
            }}
          >
            <Trash2 /> Удалить процесс
          </ProductButton>
        </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <TextField
          label="Process code / название"
          value={process.process_code}
          placeholder="CUT.LASER.SHEET"
          list="schema4-process-codes"
          onChange={(process_code) => onChange({ ...process, process_code })}
        />
        <TextField
          label="Основная ставка"
          value={process.rate_id}
          placeholder="svc:laser-sheet"
          list="schema4-rate-ids"
          onChange={(rate_id) => onChange({ ...process, rate_id })}
        />
        <TextField
          label="Метод"
          value={process.method_code}
          placeholder="BAND_SAW"
          onChange={(method_code) => onChange({ ...process, method_code })}
        />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <Switch
          checked={process.requires_manual_review === true}
          onCheckedChange={(requires_manual_review) =>
            onChange({ ...process, requires_manual_review })
          }
        />
        Требуется ручная проверка расчёта
      </label>
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium">Дополнительные ставки</p>
          <ProductButton
            outlined
            size="sm"
            disabled={process.supplementary_rate_ids.length >= 8}
            onClick={() =>
              onChange({
                ...process,
                supplementary_rate_ids: [...process.supplementary_rate_ids, ""],
              })
            }
          >
            <Plus /> Добавить ставку
          </ProductButton>
        </div>
        {process.supplementary_rate_ids.length === 0 ? (
          <p className="text-sm text-text-secondary">Дополнительных ставок нет.</p>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
            {process.supplementary_rate_ids.map((rateId, index) => (
              <div key={index} className="flex items-end gap-1">
                <TextField
                  label={`Дополнительная ставка ${index + 1}`}
                  value={rateId}
                  list="schema4-rate-ids"
                  onChange={(value) => {
                    const supplementary_rate_ids = [...process.supplementary_rate_ids];
                    supplementary_rate_ids[index] = value;
                    onChange({ ...process, supplementary_rate_ids });
                  }}
                  className="flex-1"
                />
                <ProductButton
                  ghost
                  size="icon"
                  aria-label={`Удалить дополнительную ставку ${index + 1}`}
                  onClick={() =>
                    onChange({
                      ...process,
                      supplementary_rate_ids: process.supplementary_rate_ids.filter(
                        (_, itemIndex) => itemIndex !== index,
                      ),
                    })
                  }
                >
                  <Trash2 />
                </ProductButton>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium">Пределы собственного парка по осям</p>
          <ProductButton
            outlined
            size="sm"
            disabled={!primaryAxes.length || process.in_house_axis_max.length >= 8}
            onClick={() =>
              onChange({
                ...process,
                in_house_axis_max: [
                  ...process.in_house_axis_max,
                  { axis_name: primaryAxes[0]?.name ?? "", maximum: "" },
                ],
              })
            }
          >
            <Plus /> Добавить предел
          </ProductButton>
        </div>
        {!primaryAxes.length && (
          <p className="text-sm text-text-secondary">
            Пределы доступны, когда основной ставкой выбрана матрица.
          </p>
        )}
        <div className="grid gap-2 md:grid-cols-2">
          {process.in_house_axis_max.map((maximum, index) => (
            <div key={index} className="flex flex-wrap items-end gap-2 rounded-xl border border-border p-2">
              <TextField
                label="Ось"
                value={maximum.axis_name}
                list={`process-axis-${editorId}`}
                onChange={(axis_name) => {
                  const in_house_axis_max = [...process.in_house_axis_max];
                  in_house_axis_max[index] = { ...maximum, axis_name };
                  onChange({ ...process, in_house_axis_max });
                }}
                className="min-w-36 flex-1"
              />
              <NumberInput
                label="Максимум"
                value={maximum.maximum}
                onChange={(value) => {
                  const in_house_axis_max = [...process.in_house_axis_max];
                  in_house_axis_max[index] = { ...maximum, maximum: value };
                  onChange({ ...process, in_house_axis_max });
                }}
              />
              <ProductButton
                ghost
                size="icon"
                aria-label={`Удалить предел оси ${maximum.axis_name || index + 1}`}
                onClick={() =>
                  onChange({
                    ...process,
                    in_house_axis_max: process.in_house_axis_max.filter(
                      (_, itemIndex) => itemIndex !== index,
                    ),
                  })
                }
              >
                <Trash2 />
              </ProductButton>
            </div>
          ))}
        </div>
        <datalist id={`process-axis-${editorId}`}>
          {primaryAxes.map((axis) => (
            <option key={axis.name} value={axis.name}>
              {axisTitle(axis.name)}
            </option>
          ))}
        </datalist>
      </div>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-text-secondary">Примечание</span>
        <textarea
          value={process.note}
          maxLength={200}
          rows={3}
          onChange={(event) => onChange({ ...process, note: event.target.value })}
          className="min-h-24 w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-base outline-none focus-visible:ring-2 focus-visible:ring-primary/40 sm:text-sm"
        />
        <span className="text-text-secondary">{process.note.length}/200</span>
      </label>
      <InlineIssues issues={issues} />
      </div>
    </details>
  );
}

/* ------------------------------------------------------------------ */
/*  CalcDataPage                                                       */
/* ------------------------------------------------------------------ */

export default function CalcDataPage() {
  const [form, setForm] = useState<RatesForm>(emptyForm);
  const [active, setActive] = useState<ActiveRatesResponse | null>(null);
  const [history, setHistory] = useState<RevisionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [serverError, setServerError] = useState<string | null>(null);
  // Пары «станок × группа материала» без нормы времени. Публиковать с ними
  // можно — предприятие вправе чего-то не обрабатывать. Но узнать о них надо
  // здесь, а не на третьей стадии заказа: там уже утверждены заготовка и
  // маршрут, и переделывать придётся обе.
  const [gaps, setGaps] = useState<Record<string, string[]>>({});
  // Числа, которые формально верны, но похожи на опечатку. Запрещать их
  // нельзя — у каждого предприятия свои цифры; показать обязаны.
  const [odd, setOdd] = useState<string[]>([]);
  const { toast, showToast } = useToast();
  const dirty = useRef(false);
  const draftVersion = useRef<string | null>(null);
  const editGeneration = useRef(0);
  const autosaveEpoch = useRef(0);
  const saveChain = useRef<Promise<void>>(Promise.resolve());
  const [draftSaveState, setDraftSaveState] = useState<
    "idle" | "pending" | "saving" | "saved" | "error"
  >("idle");

  const load = useCallback(async () => {
    try {
      const [current, draft, revisions] = await Promise.all([
        fetchJSON<ActiveRatesResponse>("/api/calc/rates/active"),
        fetchJSON<{
          draft: Record<string, unknown> | null;
          version: string | null;
        }>("/api/calc/rates/draft").catch(
          () => ({ draft: null, version: null }),
        ),
        fetchJSON<RevisionsResponse>("/api/calc/rates/revisions").catch(() => null),
      ]);
      setActive(current);
      setHistory(revisions);
      draftVersion.current = draft.version;
      dirty.current = false;
      setDraftSaveState(draft.draft ? "saved" : "idle");
      // Черновик важнее опубликованного: он и есть незаконченная работа
      // человека, ради которой он сюда вернулся.
      setForm(formFromPack(draft?.draft ?? current.pack));
    } catch (cause) {
      setServerError(cause instanceof Error ? cause.message : "Не удалось прочитать данные");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const warnBeforeClose = (event: BeforeUnloadEvent) => {
      if (!dirty.current) return;
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warnBeforeClose);
    return () => window.removeEventListener("beforeunload", warnBeforeClose);
  }, []);

  const saveDraftSnapshot = useCallback(
    (
      snapshot: Record<string, unknown>,
      generation: number,
      epoch: number,
    ): Promise<void> => {
      const operation = saveChain.current.catch(() => undefined).then(async () => {
        if (epoch !== autosaveEpoch.current) return;
        setDraftSaveState("saving");
        try {
          const saved = await fetchJSON<{ version: string }>("/api/calc/rates/draft", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              draft: snapshot,
              expected_version: draftVersion.current,
            }),
          });
          draftVersion.current = saved.version;
          if (generation === editGeneration.current) {
            dirty.current = false;
            setDraftSaveState("saved");
          } else {
            setDraftSaveState("pending");
          }
        } catch (cause) {
          dirty.current = true;
          setDraftSaveState("error");
          setServerError(
            cause instanceof Error
              ? cause.message
              : "Черновик не сохранился — не закрывайте вкладку",
          );
          throw cause;
        }
      });
      saveChain.current = operation;
      return operation;
    },
    [],
  );

  // Автосохранение черновика: заполнение прайса — часовая работа, а вкладки
  // закрывают. Сохраняем не чаще раза в пару секунд после последней правки.
  useEffect(() => {
    if (!dirty.current) return;
    const generation = editGeneration.current;
    const epoch = autosaveEpoch.current;
    const snapshot = packFromForm(form);
    const timer = window.setTimeout(() => {
      void saveDraftSnapshot(snapshot, generation, epoch).catch(() => undefined);
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [form, saveDraftSnapshot]);

  const update = useCallback((mutate: (draft: RatesForm) => RatesForm) => {
    if (busy) return;
    dirty.current = true;
    editGeneration.current += 1;
    setDraftSaveState("pending");
    setServerError(null);
    setForm((current) => mutate(current));
  }, [busy]);

  const schema4Issues = useMemo(() => validateSchema4(form), [form]);
  const publishable = useMemo(() => {
    const legacy = canPublish(form);
    if (!legacy.ok) return legacy;
    const issue = schema4Issues[0];
    return issue
      ? { ok: false, reason: `${issue.where}: ${issue.message}` }
      : { ok: true, reason: "" };
  }, [form, schema4Issues]);
  const groups = useMemo(() => materialGroups(form), [form]);
  const rateIds = useMemo(() => availableRateIds(form), [form]);

  const check = useCallback(async () => {
    if (schema4Issues.length) {
      const issue = schema4Issues[0];
      setServerError(`${issue.where}: ${issue.message}`);
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      const result = await fetchJSON<{
        norms_missing?: Record<string, string[]>;
        implausible?: string[];
      }>(
        "/api/calc/rates/validate",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(packFromForm(form)),
        },
      );
      setGaps(result?.norms_missing ?? {});
      setOdd(result?.implausible ?? []);
      showToast("Проверка пройдена — данные можно публиковать", "success");
    } catch (cause) {
      setServerError(cause instanceof Error ? cause.message : "Данные не прошли проверку");
    } finally {
      setBusy(false);
    }
  }, [form, schema4Issues, showToast]);

  const publish = useCallback(async () => {
    if (!publishable.ok) {
      setServerError(publishable.reason);
      return;
    }
    setBusy(true);
    setServerError(null);
    autosaveEpoch.current += 1;
    const actionEpoch = autosaveEpoch.current;
    const actionGeneration = editGeneration.current;
    try {
      await flushDraftBeforeAction({
        dirty: dirty.current,
        snapshot: packFromForm(form),
        generation: actionGeneration,
        epoch: actionEpoch,
        saveSnapshot: saveDraftSnapshot,
        pendingSave: saveChain.current,
        currentGeneration: () => editGeneration.current,
      });
      const result = await fetchJSON<{ revision: string }>("/api/calc/rates/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pack: packFromForm(form),
          note,
          expected_draft_version: draftVersion.current,
        }),
      });
      dirty.current = false;
      draftVersion.current = null;
      setDraftSaveState("idle");
      setNote("");
      showToast(`Опубликовано: ${result.revision}`, "success");
      await load();
    } catch (cause) {
      setServerError(cause instanceof Error ? cause.message : "Не удалось опубликовать");
    } finally {
      setBusy(false);
    }
  }, [form, load, note, publishable, saveDraftSnapshot, showToast]);

  const rollback = useCallback(
    async (revision: string) => {
      if (
        !window.confirm(
          `Вернуть ревизию ${revision}? Текущий черновик будет сохранён в архиве и больше не перекроет выбранную версию.`,
        )
      ) {
        return;
      }
      setBusy(true);
      setServerError(null);
      autosaveEpoch.current += 1;
      const actionEpoch = autosaveEpoch.current;
      const actionGeneration = editGeneration.current;
      try {
        await flushDraftBeforeAction({
          dirty: dirty.current,
          snapshot: packFromForm(form),
          generation: actionGeneration,
          epoch: actionEpoch,
          saveSnapshot: saveDraftSnapshot,
          pendingSave: saveChain.current,
          currentGeneration: () => editGeneration.current,
        });
        await fetchJSON("/api/calc/rates/activate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            revision,
            expected_draft_version: draftVersion.current,
          }),
        });
        dirty.current = false;
        draftVersion.current = null;
        setDraftSaveState("idle");
        showToast(`Вернули ревизию ${revision}`, "success");
        await load();
      } catch (cause) {
        setServerError(cause instanceof Error ? cause.message : "Не удалось вернуть ревизию");
      } finally {
        setBusy(false);
      }
    },
    [form, load, saveDraftSnapshot, showToast],
  );

  /** Проставить один источник во все строки секции. */
  const fillSources = useCallback(
    (section: "blank_ops" | "materials" | "machines" | "norms", template: SourceForm) => {
      update((current) => {
        if (section === "materials") {
          return { ...current, materials: current.materials.map((row) => ({ ...row, source: { ...template } })) };
        }
        if (section === "norms") {
          return { ...current, norms: current.norms.map((row) => ({ ...row, source: { ...template } })) };
        }
        if (section === "machines") {
          return { ...current, machines: current.machines.map((row) => ({ ...row, source: { ...template } })) };
        }
        return { ...current, blank_ops: current.blank_ops.map((row) => ({ ...row, source: { ...template } })) };
      });
    },
    [update],
  );

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto pb-16">
      <Toast toast={toast} />

      {/* Шапка: где мы, чего не хватает и что делать дальше */}
      <div className="sticky top-0 z-10 -mx-3 space-y-2 border-b border-border bg-background px-3 pb-3 sm:-mx-6 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
          <div className="text-sm">
            {active?.configured ? (
              <>
                {/* Первым — когда, потом чем: имя ревизии человеку ничего не
                    говорит, а «данные от 26.08, 17:03» говорит всё. */}
                Действует с {formatMoment(active.activated_at)}
                <span className="text-text-secondary">
                  {" "}
                  · ревизия {active.revision}
                </span>
              </>
            ) : (
              <span className="text-text-secondary">
                Данные ещё не заведены — заполните и опубликуйте, агенты начнут считать сразу.
              </span>
            )}
            <span
              className={cn(
                "ml-2",
                draftSaveState === "error" ? "text-warning" : "text-text-secondary",
              )}
              role={draftSaveState === "error" ? "alert" : undefined}
            >
              {draftSaveState === "pending" && "· черновик ждёт сохранения"}
              {draftSaveState === "saving" && "· черновик сохраняется…"}
              {draftSaveState === "saved" && "· черновик сохранён"}
              {draftSaveState === "error" && "· черновик не сохранён"}
            </span>
          </div>
          <div className="flex w-full flex-wrap items-end gap-2 sm:w-auto">
            <TextField
              label="Комментарий к публикации"
              value={note}
              placeholder="Например: обновили ставки лазера"
              onChange={setNote}
              className="w-full sm:w-64"
            />
            <ProductButton outlined disabled={busy} onClick={() => void check()}>
              Проверить
            </ProductButton>
            <ProductButton
              disabled={busy || !publishable.ok}
              onClick={() => void publish()}
            >
              Опубликовать
            </ProductButton>
          </div>
        </div>

        {/* Причина, по которой кнопка выключена, написана словами: молчащий
            disabled оставляет человека гадать. */}
        {!publishable.ok && (
          <p className="flex items-start gap-2 text-sm text-warning">
            <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0" />
            {publishable.reason}
          </p>
        )}
        {schema4Issues.length > 1 && (
          <details className="max-w-[70ch] rounded-xl border border-warning/60 p-3 text-sm">
            <summary className="cursor-pointer font-medium text-warning">
              Все ошибки schema4: {schema4Issues.length}
            </summary>
            <ul className="mt-2 space-y-1 text-text-secondary">
              {schema4Issues.map((issue, index) => (
                <li key={`${issue.where}-${index}`}>
                  <span className="font-medium text-foreground">{issue.where}:</span>{" "}
                  {issue.message}
                </li>
              ))}
            </ul>
          </details>
        )}
        {publishable.ok && (
          <p className="flex items-center gap-2 text-sm text-success">
            <Check aria-hidden className="size-4 shrink-0" /> Форма заполнена — можно
            публиковать.
          </p>
        )}
        {serverError && (
          <p
            className="rounded-xl border border-warning/60 p-3 text-sm text-warning"
            role="alert"
          >
            {serverError}
          </p>
        )}
        {odd.length > 0 && (
          <div className="max-w-[70ch] space-y-1 rounded-xl border border-warning/60 p-3 text-sm">
            <p className="font-medium text-warning">Проверьте эти цифры</p>
            <ul className="space-y-0.5 text-text-secondary">
              {odd.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
            <p className="text-text-secondary">
              Публиковать это не мешает — возможно, так и есть. Но опечатка в
              разряде выглядит точно так же и видна только по цене.
            </p>
          </div>
        )}
        {Object.keys(gaps).length > 0 && (
          <div className="max-w-[70ch] space-y-1 rounded-xl border border-border p-3 text-sm">
            <p className="font-medium">
              Эти операции не смогут посчитать время для части материалов
            </p>
            <ul className="space-y-0.5 text-text-secondary">
              {Object.entries(gaps).map(([op, groups]) => (
                <li key={op}>
                  {MACHINE_OPS.find((item) => item.code === op)?.title ?? op} —
                  нет норм для групп: {groups.join(", ")}
                </li>
              ))}
            </ul>
            <p className="text-text-secondary">
              Публиковать это не мешает: предприятие вправе чего-то не
              обрабатывать. Но если такая операция попадёт в маршрут, расчёт
              времени по ней остановится — уже после того, как вы утвердите
              заготовку и маршрут.
            </p>
          </div>
        )}
      </div>

      <div className="space-y-5 pt-4">
        <p className="max-w-[70ch] text-sm leading-relaxed text-text-secondary">
          Минимум, с которого расчётчики уже работают: одна операция заготовки, один
          материал, один станок, накладные и политика цены. Остальное дозаполняется
          позже — публиковать можно сколько угодно раз.
        </p>

        {/* ── Операции заготовки ─────────────────────────────────── */}
        <Section
          title="Операции заготовки"
          hint="Отметьте те, что делает предприятие. Невыбранные в расчёт не попадут."
        >
          <div className="space-y-2">
            {form.blank_ops.map((row, index) => {
              const title = BLANK_OPS.find((op) => op.code === row.code)?.title ?? row.code;
              return (
                <Row key={row.code} incomplete={row.enabled && !isSourceComplete(row.source)}>
                  <div className="flex flex-wrap items-center gap-3">
                    <label className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={row.enabled}
                        onCheckedChange={(checked) =>
                          update((current) => {
                            const blank_ops = [...current.blank_ops];
                            blank_ops[index] = { ...row, enabled: checked === true };
                            return { ...current, blank_ops };
                          })
                        }
                      />
                      <span className="w-44">{title}</span>
                    </label>
                    {row.enabled && (
                      <>
                        <Select
                          value={row.rate_kind}
                          aria-label="Единица ставки"
                          onValueChange={(value) =>
                            update((current) => {
                              const blank_ops = [...current.blank_ops];
                              blank_ops[index] = { ...row, rate_kind: value };
                              return { ...current, blank_ops };
                            })
                          }
                        >
                          {RATE_KINDS.map((kind) => (
                            <SelectOption key={kind.value} value={kind.value}>
                              {kind.title}
                            </SelectOption>
                          ))}
                        </Select>
                        <NumberInput
                          label="Ставка"
                          unit="₽"
                          value={row.rate_rub}
                          onChange={(value) =>
                            update((current) => {
                              const blank_ops = [...current.blank_ops];
                              blank_ops[index] = { ...row, rate_rub: value };
                              return { ...current, blank_ops };
                            })
                          }
                        />
                      </>
                    )}
                  </div>
                  {row.enabled && (
                    <SourceFields
                      source={row.source}
                      kinds={MONEY_SOURCE_KINDS}
                      refHint="прайс предприятия, кто и когда утвердил"
                      onChange={(next) =>
                        update((current) => {
                          const blank_ops = [...current.blank_ops];
                          blank_ops[index] = { ...row, source: next };
                          return { ...current, blank_ops };
                        })
                      }
                    />
                  )}
                </Row>
              );
            })}
            <SectionFill onFill={(source) => fillSources("blank_ops", source)} kinds={MONEY_SOURCE_KINDS} />
          </div>
        </Section>

        {/* ── Материалы ──────────────────────────────────────────── */}
        <Section
          title="Материалы"
          hint="«Под заказ» означает, что цена уйдёт в КП со звёздочкой и потребует подтверждения снабжением."
        >
          <div className="space-y-2">
            {form.materials.map((row, index) => (
              <Row key={index} incomplete={!isSourceComplete(row.source)}>
                <div className="flex flex-wrap items-center gap-2">
                  <TextField
                    label="Код материала"
                    value={row.code}
                    placeholder="steel-40x"
                    onChange={(value) =>
                      update((current) => {
                        const materials = [...current.materials];
                        materials[index] = { ...row, code: value };
                        return { ...current, materials };
                      })
                    }
                    className="w-40"
                  />
                  <TextField
                    label="Марка"
                    value={row.grade}
                    placeholder="40Х"
                    onChange={(value) =>
                      update((current) => {
                        const materials = [...current.materials];
                        materials[index] = { ...row, grade: value };
                        return { ...current, materials };
                      })
                    }
                    className="w-32"
                  />
                  <TextField
                    label="Группа материала"
                    value={row.group}
                    placeholder="steel"
                    list="material-groups"
                    onChange={(value) =>
                      update((current) => {
                        const materials = [...current.materials];
                        materials[index] = { ...row, group: value };
                        return { ...current, materials };
                      })
                    }
                    className="w-32"
                  />
                  <SelectField
                    label="Наличие"
                    value={row.stock}
                    onChange={(value) =>
                      update((current) => {
                        const materials = [...current.materials];
                        materials[index] = { ...row, stock: value };
                        return { ...current, materials };
                      })
                    }
                  >
                    {STOCK_KINDS.map((kind) => (
                      <SelectOption key={kind.value} value={kind.value}>
                        {kind.title}
                      </SelectOption>
                    ))}
                  </SelectField>
                  <NumberInput
                    label="Плотность"
                    unit="кг/м³"
                    value={row.density_kg_m3}
                    onChange={(value) =>
                      update((current) => {
                        const materials = [...current.materials];
                        materials[index] = { ...row, density_kg_m3: value };
                        return { ...current, materials };
                      })
                    }
                  />
                  <NumberInput
                    label="Цена"
                    unit="₽/кг"
                    value={row.rate_rub_per_kg}
                    onChange={(value) =>
                      update((current) => {
                        const materials = [...current.materials];
                        materials[index] = { ...row, rate_rub_per_kg: value };
                        return { ...current, materials };
                      })
                    }
                  />
                  <ProductButton
                    ghost
                    size="icon"
                    aria-label="Удалить материал"
                    onClick={() =>
                      update((current) => ({
                        ...current,
                        materials: current.materials.filter((_, i) => i !== index),
                      }))
                    }
                  >
                    <Trash2 className="size-3.5" />
                  </ProductButton>
                </div>
                <SourceFields
                  source={row.source}
                  kinds={MONEY_SOURCE_KINDS}
                  refHint="прайс или КП поставщика"
                  onChange={(next) =>
                    update((current) => {
                      const materials = [...current.materials];
                      materials[index] = { ...row, source: next };
                      return { ...current, materials };
                    })
                  }
                />
              </Row>
            ))}
            <datalist id="material-groups">
              {groups.map((group) => (
                <option key={group} value={group} />
              ))}
            </datalist>
            <div className="flex items-center gap-2">
              <ProductButton
                outlined
                size="sm"
                onClick={() =>
                  update((current) => ({
                    ...current,
                    materials: [
                      ...current.materials,
                      {
                        code: "",
                        grade: "",
                        group: current.materials.at(-1)?.group ?? "",
                        stock: "stocked",
                        density_kg_m3: "",
                        rate_rub_per_kg: "",
                        source: emptySource(),
                      },
                    ],
                  }))
                }
              >
                <Plus className="size-3.5" /> Материал
              </ProductButton>
              <SectionFill onFill={(source) => fillSources("materials", source)} kinds={MONEY_SOURCE_KINDS} />
            </div>
          </div>
        </Section>

        {/* ── Станки ─────────────────────────────────────────────── */}
        <Section title="Станко-час" hint="Ставка часа работы по видам обработки.">
          <div className="space-y-2">
            {form.machines.map((row, index) => {
              const title = MACHINE_OPS.find((op) => op.code === row.code)?.title ?? row.code;
              return (
                <Row key={row.code} incomplete={row.enabled && !isSourceComplete(row.source)}>
                  <div className="flex flex-wrap items-center gap-3">
                    <label className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={row.enabled}
                        onCheckedChange={(checked) =>
                          update((current) => {
                            const machines = [...current.machines];
                            machines[index] = { ...row, enabled: checked === true };
                            return { ...current, machines };
                          })
                        }
                      />
                      <span className="w-44">{title}</span>
                    </label>
                    {row.enabled && (
                      <NumberInput
                        label="Ставка"
                        unit="₽/час"
                        value={row.rate_rub_per_hour}
                        onChange={(value) =>
                          update((current) => {
                            const machines = [...current.machines];
                            machines[index] = { ...row, rate_rub_per_hour: value };
                            return { ...current, machines };
                          })
                        }
                      />
                    )}
                  </div>
                  {row.enabled && (
                    <SourceFields
                      source={row.source}
                      kinds={MONEY_SOURCE_KINDS}
                      refHint="прайс предприятия"
                      onChange={(next) =>
                        update((current) => {
                          const machines = [...current.machines];
                          machines[index] = { ...row, source: next };
                          return { ...current, machines };
                        })
                      }
                    />
                  )}
                </Row>
              );
            })}
            <SectionFill onFill={(source) => fillSources("machines", source)} kinds={MONEY_SOURCE_KINDS} />
          </div>
        </Section>

        {/* ── Режимы резания ─────────────────────────────────────── */}
        <Section
          title="Режимы резания"
          hint="Формулы норм времени зашиты в расчётчике — здесь только режимы из справочника и ссылка на страницу."
        >
          <div className="space-y-2">
            {form.norms.map((row, index) => (
              <NormRowEditor
                key={index}
                row={row}
                groups={groups}
                onChange={(next) =>
                  update((current) => {
                    const norms = [...current.norms];
                    norms[index] = next;
                    return { ...current, norms };
                  })
                }
                onRemove={() =>
                  update((current) => ({
                    ...current,
                    norms: current.norms.filter((_, i) => i !== index),
                  }))
                }
              />
            ))}
            <div className="flex items-center gap-2">
              <ProductButton
                outlined
                size="sm"
                onClick={() =>
                  update((current) => ({
                    ...current,
                    norms: [
                      ...current.norms,
                      {
                        op_code: "turning",
                        material_group: groups[0] ?? "",
                        values: {},
                        source: emptySource(),
                      },
                    ],
                  }))
                }
              >
                <Plus className="size-3.5" /> Режим
              </ProductButton>
              <SectionFill onFill={(source) => fillSources("norms", source)} kinds={NORM_SOURCE_KINDS} />
            </div>
          </div>
        </Section>

        {/* ── Накладные ──────────────────────────────────────────── */}
        <Section title="Накладные" hint="Вспомогательное и подготовительное время, процент на обслуживание.">
          <Row incomplete={!isSourceComplete(form.overheads.source)}>
            <div className="flex flex-wrap items-center gap-3">
              <NumberInput
                label="Вспомогательное"
                unit="мин"
                value={form.overheads.t_aux_min}
                onChange={(value) =>
                  update((current) => ({
                    ...current,
                    overheads: { ...current.overheads, t_aux_min: value },
                  }))
                }
              />
              <NumberInput
                label="Обслуживание и отдых"
                unit="%"
                value={form.overheads.k_service_rest_pct}
                onChange={(value) =>
                  update((current) => ({
                    ...current,
                    overheads: { ...current.overheads, k_service_rest_pct: value },
                  }))
                }
              />
              <NumberInput
                label="Подготовительное"
                unit="мин"
                value={form.overheads.t_setup_min}
                onChange={(value) =>
                  update((current) => ({
                    ...current,
                    overheads: { ...current.overheads, t_setup_min: value },
                  }))
                }
              />
            </div>
            <SourceFields
              source={form.overheads.source}
              kinds={NORM_SOURCE_KINDS}
              refHint="справочник, издание, страница"
              onChange={(next) =>
                update((current) => ({
                  ...current,
                  overheads: { ...current.overheads, source: next },
                }))
              }
            />
          </Row>
        </Section>

        {/* ── Доп. расходы ───────────────────────────────────────── */}
        <Section title="Доп. расходы" hint="Упаковка и доставка — добавляются в КП, если заданы.">
          <div className="space-y-2">
            {form.extras.map((row, index) => {
              const title = EXTRA_CODES.find((extra) => extra.code === row.code)?.title ?? row.code;
              return (
                <Row key={row.code} incomplete={row.enabled && !isSourceComplete(row.source)}>
                  <div className="flex flex-wrap items-center gap-3">
                    <label className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={row.enabled}
                        onCheckedChange={(checked) =>
                          update((current) => {
                            const extras = [...current.extras];
                            extras[index] = { ...row, enabled: checked === true };
                            return { ...current, extras };
                          })
                        }
                      />
                      <span className="w-44">{title}</span>
                    </label>
                    {row.enabled && (
                      <NumberInput
                        label="Ставка"
                        unit="₽"
                        value={row.rate_rub}
                        onChange={(value) =>
                          update((current) => {
                            const extras = [...current.extras];
                            extras[index] = { ...row, rate_rub: value };
                            return { ...current, extras };
                          })
                        }
                      />
                    )}
                  </div>
                  {row.enabled && (
                    <SourceFields
                      source={row.source}
                      kinds={MONEY_SOURCE_KINDS}
                      refHint="прайс предприятия"
                      onChange={(next) =>
                        update((current) => {
                          const extras = [...current.extras];
                          extras[index] = { ...row, source: next };
                          return { ...current, extras };
                        })
                      }
                    />
                  )}
                </Row>
              );
            })}
          </div>
        </Section>

        {form.schema_version >= 3 && (
          <>
            <datalist id="schema4-process-codes">
              {Object.entries(PROCESS_TITLES).map(([code, title]) => (
                <option key={code} value={code}>
                  {title}
                </option>
              ))}
            </datalist>
            <datalist id="schema4-rate-ids">
              {rateIds.map((rateId) => (
                <option key={rateId} value={rateId} />
              ))}
            </datalist>

            <Section
              title="Реестр ставок"
              hint="Одно значение подходит для фиксированного тарифа. Матрица раскрывается по диапазонам одной или двух осей; пустая ячейка означает, что тарифа для диапазона нет."
            >
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  <ProductButton
                    outlined
                    size="sm"
                    onClick={() =>
                      update((current) => ({
                        ...current,
                        rate_registry: [...current.rate_registry, newScalarRate()],
                      }))
                    }
                  >
                    <Plus /> Скалярная ставка
                  </ProductButton>
                  <ProductButton
                    outlined
                    size="sm"
                    onClick={() =>
                      update((current) => ({
                        ...current,
                        rate_registry: [...current.rate_registry, newMatrixRate()],
                      }))
                    }
                  >
                    <Plus /> Матричная ставка
                  </ProductButton>
                </div>
                {form.rate_registry.length === 0 && (
                  <p className="text-sm text-text-secondary">
                    Явных ставок пока нет. Добавьте ставку для процессов парка.
                  </p>
                )}
                {form.rate_registry.map((rate, index) => {
                  const label = `Ставка ${rate.rate_id.trim() || index + 1}`;
                  return (
                    <RateEditor
                      key={index}
                      rate={rate}
                      displayName={rateTitle(rate.rate_id, form.process_park)}
                      issues={schema4Issues.filter((issue) => issue.where.startsWith(label))}
                      onChange={(next) =>
                        update((current) => {
                          const rate_registry = [...current.rate_registry];
                          rate_registry[index] = next;
                          return { ...current, rate_registry };
                        })
                      }
                      onRemove={() =>
                        update((current) => ({
                          ...current,
                          rate_registry: current.rate_registry.filter(
                            (_, itemIndex) => itemIndex !== index,
                          ),
                        }))
                      }
                    />
                  );
                })}
              </div>
            </Section>

            <Section
              title="Парк процессов"
              hint="Каждый process_code связывает операцию предприятия с основной и дополнительными ставками. Название рядом берётся из каталога операций."
            >
              <div className="space-y-3">
                <ProductButton
                  outlined
                  size="sm"
                  onClick={() =>
                    update((current) => ({
                      ...current,
                      process_park: [...current.process_park, newProcessParkRow()],
                    }))
                  }
                >
                  <Plus /> Добавить процесс
                </ProductButton>
                {form.process_park.map((process, index) => {
                  const label = `Процесс ${process.process_code.trim() || index + 1}`;
                  return (
                    <ProcessEditor
                      key={index}
                      process={process}
                      editorId={String(index)}
                      primaryRate={form.rate_registry.find(
                        (rate) => rate.rate_id.trim() === process.rate_id.trim(),
                      )}
                      issues={schema4Issues.filter((issue) => issue.where.startsWith(label))}
                      onChange={(next) =>
                        update((current) => {
                          const process_park = [...current.process_park];
                          process_park[index] = next;
                          return { ...current, process_park };
                        })
                      }
                      onRemove={() =>
                        update((current) => ({
                          ...current,
                          process_park: current.process_park.filter(
                            (_, itemIndex) => itemIndex !== index,
                          ),
                        }))
                      }
                    />
                  );
                })}
              </div>
            </Section>
          </>
        )}

        {/* ── Политика цены ──────────────────────────────────────── */}
        <Section title="Политика цены" hint="Как из себестоимости получается цена в КП.">
          <div className="flex flex-wrap items-center gap-4 rounded-sm border border-border p-2.5">
            <label className="flex items-center gap-2 text-sm">
              <span className="text-text-secondary">База</span>
              <Select
                value={form.pricing.margin_basis}
                aria-label="База наценки"
                onValueChange={(value) =>
                  update((current) => ({
                    ...current,
                    pricing: { ...current.pricing, margin_basis: value },
                  }))
                }
              >
                {MARGIN_BASIS.map((basis) => (
                  <SelectOption key={basis.value} value={basis.value}>
                    {basis.title}
                  </SelectOption>
                ))}
              </Select>
            </label>
            <NumberInput
              label="Наценка"
              unit="%"
              value={form.pricing.margin_percent}
              onChange={(value) =>
                update((current) => ({
                  ...current,
                  pricing: { ...current.pricing, margin_percent: value },
                }))
              }
            />
            {form.schema_version >= 4 && (
              <>
                <NumberInput
                  label="Наценка на материал"
                  unit="%"
                  value={form.pricing.material_markup_percent}
                  onChange={(value) =>
                    update((current) => ({
                      ...current,
                      pricing: {
                        ...current.pricing,
                        material_markup_percent: value,
                      },
                    }))
                  }
                />
                <label className="flex items-center gap-2 text-sm">
                  <span className="text-text-secondary">Ставки уже с НДС</span>
                  <Switch
                    checked={form.pricing.rates_include_vat}
                    onCheckedChange={(checked) =>
                      update((current) => ({
                        ...current,
                        pricing: {
                          ...current.pricing,
                          rates_include_vat: checked,
                        },
                      }))
                    }
                  />
                </label>
              </>
            )}
            <label className="flex items-center gap-2 text-sm">
              <span className="text-text-secondary">НДС включён</span>
              <Switch
                checked={form.pricing.vat_included}
                onCheckedChange={(checked) =>
                  update((current) => ({
                    ...current,
                    pricing: {
                      ...current.pricing,
                      vat_included: checked,
                      // Без НДС ставка обязана быть нулевой — движок иначе
                      // откажет, и человек не поймёт почему.
                      vat_rate_pct: checked ? current.pricing.vat_rate_pct : "0",
                    },
                  }))
                }
              />
            </label>
            <NumberInput
              label="Ставка НДС"
              unit="%"
              value={form.pricing.vat_rate_pct}
              onChange={(value) =>
                update((current) => ({
                  ...current,
                  pricing: { ...current.pricing, vat_rate_pct: value },
                }))
              }
            />
            <NumberInput
              label="КП действует"
              unit="дней"
              value={form.pricing.valid_days}
              onChange={(value) =>
                update((current) => ({
                  ...current,
                  pricing: { ...current.pricing, valid_days: value },
                }))
              }
            />
            <label className="flex items-center gap-2 text-sm">
              <span className="text-text-secondary">Округление</span>
              <Select
                value={form.pricing.rounding}
                aria-label="Округление"
                onValueChange={(value) =>
                  update((current) => ({
                    ...current,
                    pricing: { ...current.pricing, rounding: value },
                  }))
                }
              >
                {ROUNDING.map((rule) => (
                  <SelectOption key={rule.value} value={rule.value}>
                    {rule.title}
                  </SelectOption>
                ))}
              </Select>
            </label>
          </div>
        </Section>

        {/* ── История ────────────────────────────────────────────── */}
        {history?.revisions?.length ? (
          <Section title="История" hint="Каждое сохранение — отдельная версия. Прошлую всегда можно вернуть.">
            <ul className="space-y-1.5 text-sm">
              {history.revisions.slice(0, 20).map((entry) => (
                <li
                  key={entry.revision}
                  className="flex flex-wrap items-center justify-between gap-2 border-b border-border/40 py-1.5"
                >
                  <span className="flex items-center gap-2">
                    <span>{entry.revision}</span>
                    {entry.active && (
                      <span className="rounded-lg border border-success/60 px-2 py-0.5 text-sm text-success">
                        действует
                      </span>
                    )}
                    {entry.note && (
                      <span className="text-text-secondary">{entry.note}</span>
                    )}
                  </span>
                  <span className="flex items-center gap-2 text-text-secondary">
                    {formatMoment(entry.at)}
                    {!entry.active && (
                      <ProductButton
                        ghost
                        size="sm"
                        disabled={busy}
                        onClick={() => void rollback(entry.revision)}
                      >
                        <RotateCcw className="size-3" /> Вернуть
                      </ProductButton>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </Section>
        ) : null}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Вспомогательные компоненты                                         */
/* ------------------------------------------------------------------ */

/**
 * «Проставить источник во всей секции».
 *
 * Без этого сорок материалов из одного прайса физически не ввести. Значение
 * при этом записывается в каждую строку явно и остаётся видимым — наследовать
 * источник по умолчанию нельзя: тогда он перестанет быть утверждением о
 * конкретной цифре.
 */
function SectionFill({
  kinds,
  onFill,
}: {
  kinds: readonly { value: string; title: string }[];
  onFill: (source: SourceForm) => void;
}) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState<SourceForm>({ kind: "", ref: "", as_of: today() });
  if (!open) {
    return (
      <ProductButton ghost size="sm" onClick={() => setOpen(true)}>
        Проставить источник во все строки
      </ProductButton>
    );
  }
  return (
    <div className="space-y-1.5 rounded-sm border border-border p-2.5">
      <p className="max-w-[70ch] text-sm leading-relaxed text-text-secondary">
        Источник запишется в каждую строку секции — видимым значением, а не по умолчанию.
      </p>
      <SourceFields
        source={source}
        kinds={kinds}
        refHint="прайс или справочник"
        onChange={setSource}
      />
      <div className="flex gap-2">
        <ProductButton
          size="sm"
          disabled={!isSourceComplete(source)}
          onClick={() => {
            onFill(source);
            setOpen(false);
          }}
        >
          Проставить
        </ProductButton>
        <ProductButton ghost size="sm" onClick={() => setOpen(false)}>
          Отмена
        </ProductButton>
      </div>
    </div>
  );
}

/** Строка режима: набор числовых полей задаётся выбранной операцией. */
function NormRowEditor({
  row,
  groups,
  onChange,
  onRemove,
}: {
  row: NormRow;
  groups: string[];
  onChange: (next: NormRow) => void;
  onRemove: () => void;
}) {
  const fields = NORM_FIELDS[row.op_code] ?? [];
  return (
    <Row incomplete={!isSourceComplete(row.source)}>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={row.op_code}
          aria-label="Операция"
          onValueChange={(value) =>
            // Меняя операцию, обнуляем значения: набор параметров у неё
            // другой, и оставшиеся от прошлой ушли бы в пак лишними ключами.
            onChange({ ...row, op_code: value, values: {} })
          }
        >
          {MACHINE_OPS.map((op) => (
            <SelectOption key={op.code} value={op.code}>
              {op.title}
            </SelectOption>
          ))}
        </Select>
        <TextField
          label="Группа материала"
          value={row.material_group}
          placeholder="steel"
          list="material-groups"
          onChange={(material_group) => onChange({ ...row, material_group })}
          className="w-40"
        />
        {fields.map((field) => (
          <NumberInput
            key={field.key}
            label={field.label}
            unit={field.unit}
            value={row.values[field.key] ?? ""}
            onChange={(value) =>
              onChange({ ...row, values: { ...row.values, [field.key]: value } })
            }
          />
        ))}
        <ProductButton ghost size="sm" aria-label="Удалить режим" onClick={onRemove}>
          <Trash2 className="size-3.5" />
        </ProductButton>
      </div>
      {groups.length > 0 && !groups.includes(row.material_group.trim()) && row.material_group && (
        <p className="text-sm text-warning">
          Такой группы нет ни у одного материала — расчёт времени её не найдёт.
        </p>
      )}
      <SourceFields
        source={row.source}
        kinds={NORM_SOURCE_KINDS}
        refHint="справочник, издание, страница"
        onChange={(next) => onChange({ ...row, source: next })}
      />
    </Row>
  );
}
