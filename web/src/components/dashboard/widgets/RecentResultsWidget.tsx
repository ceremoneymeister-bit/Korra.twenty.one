/* eslint-disable react-refresh/only-export-components -- виджет и его каталожное описание намеренно живут вместе */
import { useState } from "react";
import { Download, Layers3 } from "lucide-react";
import { Link } from "react-router";

import type {
  DashboardWidget,
  DashboardWidgetBodyProps,
} from "@/components/dashboard/widget-types";
import {
  ErrorNote,
  FirstStep,
  LoadingNote,
  StaleMark,
  useDashboardSection,
  useNowSeconds,
} from "@/components/dashboard/widget-states";
import { ArtifactCover } from "@/components/dashboard/visuals";
import { downloadWorkspaceFile } from "@/lib/chat-attachments";
import {
  ARTIFACT_KIND_LABELS,
  artifactFolderHref,
  dashboardTimeZone,
  formatMoment,
  plural,
  type DashboardArtifact,
} from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

/**
 * «Артефакты» — что уже готово и можно забрать.
 *
 * Источник — рабочая папка установки в новой раскладке K21-146: результаты
 * каждого агента с его именем, общие материалы и прежние файлы корня.
 * Загрузки владельца из чатов сюда не попадают: это не результат работы.
 *
 * Обложка — настоящая: миниатюра картинки, первые строки текста, формат и
 * имя файла. Нажатие открывает файл в разделе «Файлы» на своём месте,
 * отдельная кнопка скачивает его.
 */

const COUNT = { s: 1, m: 3, l: 3 } as const;

function ArtifactsBody({ size = "m" }: DashboardWidgetBodyProps) {
  const view = useDashboardSection("artifacts");
  const now = useNowSeconds(view.phase === "ready" ? view.state.generated_at : 0);

  if (view.phase === "loading") return <LoadingNote text="Ищем готовые файлы…" />;
  if (view.phase === "error") {
    return (
      <ErrorNote
        size={size}
        title="Не удалось прочитать файлы"
        detail="Список готовых материалов не пришёл. Сами файлы на месте — они есть в разделе «Файлы»."
        onRetry={view.retry}
      />
    );
  }
  const artifacts = view.section;
  if (artifacts.status === "empty" || artifacts.items.length === 0) {
    return (
      <FirstStep
        size={size}
        title="Готовых файлов пока нет"
        text="Попросите агента подготовить документ, таблицу или презентацию — готовое появится здесь."
        action={{ label: "Поручить агенту", to: "/agents" }}
      />
    );
  }

  const timeZone = dashboardTimeZone(view.state);
  const items = artifacts.items.slice(0, COUNT[size]);
  const recent = artifacts.recent_count;

  return (
    <div className={cn("kdw-artifacts-body", `kdw-artifacts-body--${size}`)}>
      <ul className={cn("kdw-artifacts", `kdw-artifacts--${size}`)} aria-label="Последние готовые файлы">
        {items.map((item, index) => (
          <ArtifactTile
            key={item.path}
            item={item}
            large={size === "l" && index === 0}
            lines={size === "l" && index === 0 ? 3 : size === "s" ? 2 : 0}
            compact={size === "s"}
            meta={`${ARTIFACT_KIND_LABELS[item.kind] ?? "Файл"} · ${formatMoment(item.modified_at, now, timeZone)}`}
          />
        ))}
      </ul>
      {size === "s" ? null : (
        <p className="kdw-artifacts-foot">
          <Layers3 className="size-3.5 shrink-0" aria-hidden />
          <span className="min-w-0 truncate">
            {recent > 0
              ? `${recent} ${plural(recent, ["новый файл", "новых файла", "новых файлов"])} за неделю`
              : "За неделю новых файлов не было"}
            {artifacts.truncated ? " (учтены не все папки)" : ""}
          </span>
        </p>
      )}
      <StaleMark stale={view.stale} />
    </div>
  );
}

function ArtifactTile({
  compact,
  item,
  large,
  lines,
  meta,
}: {
  compact: boolean;
  item: DashboardArtifact;
  large: boolean;
  lines: number;
  meta: string;
}) {
  const [downloading, setDownloading] = useState(false);
  const [failed, setFailed] = useState(false);
  const download = () => {
    if (downloading) return;
    setDownloading(true);
    setFailed(false);
    void downloadWorkspaceFile(item.path, item.name)
      .catch(() => setFailed(true))
      .finally(() => setDownloading(false));
  };
  return (
    <li className={cn("kdw-artifact", large && "kdw-artifact--main")} data-artifact-kind={item.kind}>
      <Link
        to={artifactFolderHref(item)}
        className="kdw-artifact-stage"
        aria-label={`Открыть «${item.name}» в разделе «Файлы»`}
      >
        <ArtifactCover item={item} large={large} lines={lines} />
      </Link>
      <div className="kdw-artifact-caption">
        <div className="min-w-0 flex-1">
          <p className="kdw-artifact-name" title={item.name}>
            {item.name}
          </p>
          {compact ? null : (
            <p className="kdw-artifact-meta">
              {failed ? "Не удалось скачать — файл мог быть перемещён" : meta}
            </p>
          )}
          {large && item.agent ? <p className="kdw-artifact-meta">Автор: {item.agent}</p> : null}
        </div>
        {compact ? null : (
          <button
            type="button"
            className="kdw-icon-button"
            onClick={download}
            disabled={downloading}
            aria-label={`Скачать «${item.name}»`}
          >
            <Download className="size-4" aria-hidden />
          </button>
        )}
      </div>
    </li>
  );
}

export const RECENT_RESULTS_WIDGET: DashboardWidget = {
  id: "recent-results",
  title: "Артефакты",
  purpose: "Готовые документы, таблицы, презентации и изображения, которые сделали агенты.",
  action: { label: "Открыть файлы", short: "Файлы", to: "/files" },
  Body: ArtifactsBody,
};
