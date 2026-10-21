import type { ReactNode } from "react";
import { Link } from "react-router";
import { ArrowLeft, type LucideIcon } from "lucide-react";
import { HERMES_BASE_PATH } from "@/lib/api";
import {
  buildOrderLink,
  panelAddress,
  type MarketplaceItem,
  type OrderSubject,
} from "@/lib/marketplace-catalog";
import { MARKETPLACE_ICONS, type MarketplaceIconName } from "@/lib/marketplace-icons";
import { cn } from "@/lib/utils";

export const CARD_STYLE = { borderRadius: "var(--neo-radius-control)" } as const;
export const CARD_CLASS = "relative flex flex-col gap-3 bg-[var(--neo-surface)] p-4 shadow-[var(--neo-depth-1)]";

const BUTTON_CLASS = "inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg border px-4 py-2.5 text-sm font-semibold outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background-base [&>svg]:size-4";
/** Главное действие: платный заказ, подключение, переподключение. */
export const PRIMARY_BUTTON_CLASS = `${BUTTON_CLASS} border-primary bg-primary text-primary-foreground hover:bg-primary/90`;
/** Спокойное действие: бесплатное, ознакомительное. */
export const QUIET_BUTTON_CLASS = `${BUTTON_CLASS} border-border bg-background/55 text-foreground hover:border-primary/45 hover:bg-primary/[0.06]`;

export function iconFor(name: MarketplaceIconName): LucideIcon {
  return MARKETPLACE_ICONS[name];
}

export function IconTile({ icon: Icon, children, className }: { icon?: LucideIcon; children?: ReactNode; className?: string }) {
  return (
    <span
      className={cn("flex size-10 shrink-0 items-center justify-center bg-[var(--neo-surface)] shadow-[var(--neo-inset-compact)]", className)}
      style={CARD_STYLE}
    >
      {children ?? (Icon ? <Icon aria-hidden className="size-[20px]" strokeWidth={1.75} /> : null)}
    </span>
  );
}

export function Price({ price, note, large }: { price: string; note?: string; large?: boolean }) {
  return (
    <p className="grid gap-0.5" data-price>
      <span className={cn("font-semibold leading-snug", large ? "text-2xl" : "text-lg")}>{price}</span>
      {note && <span className="text-xs text-[var(--neo-text-secondary)]">{note}</span>}
    </p>
  );
}

export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="inline-flex min-h-[44px] w-fit items-center gap-2 rounded-lg text-sm font-medium text-[var(--neo-text-secondary)] outline-none transition-colors hover:text-[var(--neo-text-primary)] focus-visible:ring-2 focus-visible:ring-primary/40"
    >
      <ArrowLeft aria-hidden className="size-4" />
      {children}
    </Link>
  );
}

/** Заказ — чат с нами в Telegram с готовым сообщением, как кнопки сайта. */
export function OrderLink({ subject, label, className }: {
  subject: OrderSubject;
  label: string;
  className?: string;
}) {
  const panel = panelAddress(window.location.host, HERMES_BASE_PATH);
  return (
    <a
      href={buildOrderLink(subject, panel)}
      target="_blank"
      rel="noopener"
      className={cn(PRIMARY_BUTTON_CLASS, className)}
    >
      {label}
    </a>
  );
}

/** Заказ на карточке: рамка и лёгкая заливка акцентом, сплошной акцент — только на странице позиции. */
const CARD_ORDER_CLASS = "relative z-10 bg-primary/15 text-foreground hover:bg-primary/25";

export function ItemOrderButton({ item, label, solid }: { item: MarketplaceItem; label: string; solid?: boolean }) {
  return <OrderLink subject={{ kind: "item", item }} label={label} className={solid ? "relative z-10" : CARD_ORDER_CLASS} />;
}
