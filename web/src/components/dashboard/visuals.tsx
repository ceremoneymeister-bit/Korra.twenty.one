/* eslint-disable react-refresh/only-export-components -- визуальные примитивы карточек и их чистые правила живут вместе */
/**
 * Визуальный слой дашборда, перенесённый из принятой 21.09 визуальной v1
 * (`web/prototypes/dashboard/`): аватары агентов, обложки артефактов,
 * столбики показателя, кольцо квоты.
 *
 * Разница с макетом одна и принципиальная: здесь ничего не придумано. Обложка
 * несёт настоящее имя файла, его формат и — для текста — первые строки
 * самого файла; столбики — настоящие дневные числа; аватар строится из имени
 * агента и его готового шаблона, а не из портрета «на вкус».
 */

import { useEffect, useState, type CSSProperties } from "react";
import { BarChart3, Sparkles } from "lucide-react";

import { authedFetch } from "@/lib/api";
import { artifactUrl } from "@/lib/chat-artifacts";
import {
  ARTIFACT_KIND_LABELS,
  type DashboardArtifact,
} from "@/lib/dashboard-state";
import { cn } from "@/lib/utils";

// ── Аватар агента ─────────────────────────────────────────────────────────

/** Знак по готовому шаблону агента: у Дизайнера — «цветок», у аналитиков —
 *  столбики, у главного — искра. Остальные узнаются по первой букве. */
export type AgentGlyph = "design" | "analysis" | "main" | "letter";

const TONES = ["graphite", "lime", "stone", "sand", "mist"] as const;
export type AvatarTone = (typeof TONES)[number];

export function agentGlyph(template: string | null | undefined, isMain: boolean): AgentGlyph {
  const name = (template ?? "").toLowerCase();
  if (/design|дизайн/.test(name)) return "design";
  if (/analy|research|аналит|исслед/.test(name)) return "analysis";
  return isMain ? "main" : "letter";
}

/** Устойчивый тон по имени: один и тот же агент одинаков на любом экране. */
export function avatarTone(key: string, glyph: AgentGlyph): AvatarTone {
  if (glyph === "design") return "graphite";
  if (glyph === "analysis") return "lime";
  if (glyph === "main") return "stone";
  let hash = 0;
  for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return TONES[hash % TONES.length];
}

export interface AgentAvatarProps {
  label: string;
  profile: string;
  template?: string | null;
  size?: "sm" | "md";
  /** Точка активности в углу: работает / ждёт решения. */
  busy?: boolean;
  className?: string;
}

export function AgentAvatar({
  busy,
  className,
  label,
  profile,
  size = "md",
  template,
}: AgentAvatarProps) {
  const glyph = agentGlyph(template, profile === "");
  const tone = avatarTone(profile || "main", glyph);
  return (
    <span
      aria-hidden
      className={cn("kdw-avatar", `kdw-avatar--${tone}`, size === "sm" && "kdw-avatar--sm", className)}
      data-glyph={glyph}
    >
      {glyph === "design" ? (
        <span className="kdw-avatar-flower">✳</span>
      ) : glyph === "analysis" ? (
        <span className="kdw-avatar-bars">
          <i />
          <i />
          <i />
          <i />
        </span>
      ) : glyph === "main" ? (
        <Sparkles className="kdw-avatar-spark" strokeWidth={1.6} />
      ) : (
        <span className="kdw-avatar-letter">{label.trim().slice(0, 1).toUpperCase() || "·"}</span>
      )}
      {busy ? <span className="kdw-avatar-dot" /> : null}
    </span>
  );
}

/** Три бегущих столбика «идёт работа». Анимация гаснет при reduced motion. */
export function ActivityBars() {
  return (
    <span aria-hidden className="kdw-activity">
      <i />
      <i />
      <i />
    </span>
  );
}

// ── Столбики показателя ───────────────────────────────────────────────────

export interface MetricBarsProps {
  values: readonly number[];
  /** Голосом: что это за ряд и за какой период. */
  label: string;
  className?: string;
}

/**
 * Дневные столбики. Высота — доля от максимума периода; день без событий —
 * тонкая черта, а не пропуск: пустой день тоже факт. Последний столбик —
 * сегодняшний, он выделен цветом акцента, как в принятой v1.
 */
export function MetricBars({ className, label, values }: MetricBarsProps) {
  const max = Math.max(1, ...values);
  return (
    <div className={cn("kdw-bars", className)} role="img" aria-label={label}>
      <span className="kdw-bars-grid" aria-hidden />
      {values.map((value, index) => (
        <span
          key={index}
          aria-hidden
          className={cn("kdw-bar", index === values.length - 1 && "kdw-bar--today", value === 0 && "kdw-bar--zero")}
          style={{ height: value === 0 ? undefined : `${Math.max(6, (value / max) * 100)}%` }}
        />
      ))}
    </div>
  );
}

// ── Кольцо квоты ──────────────────────────────────────────────────────────

export interface QuotaRingProps {
  percent: number;
  level: "normal" | "warn" | "critical";
  /** Размер задаёт CSS (`.kdw-ring--s/m/l`): на узком полотне кольцо меньше. */
  size?: "s" | "m" | "l";
}

export function QuotaRing({ level, percent, size = "m" }: QuotaRingProps) {
  const clamped = Math.max(0, Math.min(100, percent));
  const style = { "--kdw-ring": `${clamped * 3.6}deg` } as CSSProperties;
  return (
    <span
      aria-hidden
      className={cn("kdw-ring", `kdw-ring--${level}`, `kdw-ring--size-${size}`)}
      style={style}
    >
      <span className="kdw-ring-core">
        <strong>{Math.round(clamped)}</strong>
        <small>%</small>
      </span>
    </span>
  );
}

// ── Обложка артефакта ─────────────────────────────────────────────────────

const IMAGE_PREVIEW_LIMIT = 8 * 1024 * 1024;

/**
 * Настоящая миниатюра изображения.
 *
 * В панели с токеном сессии картинку забираем авторизованным запросом и
 * показываем object URL: токен нельзя класть в адрес. В кабинете токена в
 * странице нет, и браузер ходит обычной ссылкой через прокси.
 */
function useWorkspaceImage(item: DashboardArtifact, enabled: boolean): string | null {
  const [src, setSrc] = useState<string | null>(null);
  const token = typeof window === "undefined" ? "" : (window.__HERMES_SESSION_TOKEN__ ?? "");
  useEffect(() => {
    if (!enabled || !token) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    void authedFetch(`/api/files/download?${new URLSearchParams({ path: item.path, inline: "1" })}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("preview");
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [enabled, item.path, item.modified_at, token]);
  if (!enabled) return null;
  return token ? src : artifactUrl(item.path);
}

export interface ArtifactCoverProps {
  item: DashboardArtifact;
  /** Главная обложка крупнее: заголовок набран крупнее. */
  large?: boolean;
  /**
   * Сколько первых строк текста показать на обложке. Строка либо видна
   * целиком, либо её нет: на низкой обложке плитки 2×1 место есть только
   * под заголовок, и полуобрезанная строка читалась бы как поломка.
   */
  lines?: number;
}

export function ArtifactCover({ item, large = false, lines: lineCount = 0 }: ArtifactCoverProps) {
  const [broken, setBroken] = useState(false);
  const wantsImage = item.kind === "image" && item.size <= IMAGE_PREVIEW_LIMIT && !broken;
  const image = useWorkspaceImage(item, wantsImage);
  const kindLabel = ARTIFACT_KIND_LABELS[item.kind] ?? "Файл";
  const extension = item.ext ? item.ext.toUpperCase() : kindLabel;

  if (wantsImage && image) {
    return (
      <span className="kdw-cover kdw-cover--image" data-kind={item.kind}>
        <img alt="" src={image} loading="lazy" onError={() => setBroken(true)} />
      </span>
    );
  }

  const heading = (item.title || item.name.replace(/\.[^.]+$/, "")).trim() || item.name;
  const lines = item.excerpt?.slice(0, lineCount) ?? [];
  return (
    <span aria-hidden className={cn("kdw-cover", `kdw-cover--${coverStyle(item.kind)}`)} data-kind={item.kind}>
      <span className="kdw-cover-brand">{item.agent ?? (item.section === "shared" ? "Общие материалы" : "Korra")}</span>
      <strong className={cn("kdw-cover-title", large && "kdw-cover-title--large")}>{heading}</strong>
      {lines.length ? (
        <span className="kdw-cover-lines">
          {lines.map((line, index) => (
            <span key={index}>{line}</span>
          ))}
        </span>
      ) : item.kind === "table" ? (
        <span className="kdw-cover-grid">
          {Array.from({ length: 12 }, (_, index) => (
            <i key={index} />
          ))}
        </span>
      ) : item.kind === "presentation" ? (
        <span className="kdw-cover-orbit">
          <i />
          <i />
        </span>
      ) : item.kind === "audio" || item.kind === "video" ? (
        <BarChart3 className="kdw-cover-icon" strokeWidth={1.4} />
      ) : null}
      <span className="kdw-cover-foot">
        <span>{kindLabel}</span>
        <span>{extension}</span>
      </span>
    </span>
  );
}

function coverStyle(kind: DashboardArtifact["kind"]): string {
  switch (kind) {
    case "presentation":
      return "deck";
    case "table":
      return "sheet";
    case "pdf":
      return "pdf";
    case "image":
    case "video":
    case "audio":
      return "media";
    default:
      return "paper";
  }
}
