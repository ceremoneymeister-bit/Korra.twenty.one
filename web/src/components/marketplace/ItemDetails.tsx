import { Check } from "lucide-react";
import type { ReactNode } from "react";
import {
  MARKETPLACE_ORDER_STEPS,
  orderLabel,
  type MarketplaceItem,
  type MarketplaceSection,
} from "@/lib/marketplace-catalog";
import { CARD_CLASS, CARD_STYLE, IconTile, ItemOrderButton, Price, iconFor } from "./ui";

function Block({ title, children, wide }: { title: string; children: ReactNode; wide?: boolean }) {
  return (
    <section className={`${CARD_CLASS} gap-4 p-5 ${wide ? "md:col-span-2" : ""}`} style={CARD_STYLE} aria-label={title}>
      <h3 className="text-lg font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function CheckList({ items }: { items: string[] }) {
  return (
    <ul className="grid gap-2.5 text-sm leading-relaxed">
      {items.map((entry) => (
        <li key={entry} className="flex gap-3">
          <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-[var(--neo-text-secondary)]" />
          <span>{entry}</span>
        </li>
      ))}
    </ul>
  );
}

/** Страница позиции: что делает, что получите, что нужно, как будет. */
export function ItemDetails({ item, section }: { item: MarketplaceItem; section: MarketplaceSection }) {
  const label = orderLabel(item, section);
  const Icon = iconFor(item.icon);
  return (
    <div className="grid gap-4" data-item-page={item.id}>
      <header
        className="grid gap-5 bg-[var(--neo-surface)] p-5 shadow-[var(--neo-depth-2)] sm:p-7 md:grid-cols-[1fr_auto] md:items-end"
        style={{ borderRadius: "var(--neo-radius-card)" }}
      >
        <div className="grid gap-3">
          <IconTile icon={Icon} className="size-14" />
          <h2 tabIndex={-1} className="text-2xl font-semibold leading-tight sm:text-[32px]">{item.name}</h2>
          <p className="max-w-[60ch] text-base leading-relaxed text-[var(--neo-text-secondary)]">{item.tagline}</p>
        </div>
        <div className="grid gap-3 md:min-w-[220px]">
          {item.price && <Price price={item.price} note={item.price_note} large />}
          {label && <ItemOrderButton item={item} label={label} solid />}
        </div>
      </header>

      {item.presentation && (
        <section className="grid gap-3" aria-label="Презентация" data-presentation>
          <h3 className="text-lg font-semibold">Презентация</h3>
          <video
            className="w-full bg-[var(--neo-surface)] shadow-[var(--neo-depth-1)]"
            style={{ borderRadius: "var(--neo-radius-control)" }}
            src={item.presentation.src}
            poster={item.presentation.poster}
            controls
            preload="metadata"
          />
          {item.presentation.caption && <p className="text-sm text-[var(--neo-text-secondary)]">{item.presentation.caption}</p>}
        </section>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {item.does && item.does.length > 0 && <Block title="Что делает"><CheckList items={item.does} /></Block>}
        {item.gives && item.gives.length > 0 && <Block title="Что вы получите"><CheckList items={item.gives} /></Block>}
        {item.needs && item.needs.length > 0 && <Block title="Что нужно"><CheckList items={item.needs} /></Block>}
        {label && (
          <Block title="Как это будет" wide={!item.needs?.length}>
            <ol className="grid gap-3 text-sm leading-relaxed">
              {MARKETPLACE_ORDER_STEPS.map((step, index) => (
                <li key={step} className="flex gap-3">
                  <span
                    aria-hidden
                    className="flex size-7 shrink-0 items-center justify-center rounded-full bg-[var(--neo-surface)] text-xs font-semibold shadow-[var(--neo-inset-compact)]"
                  >
                    {index + 1}
                  </span>
                  <span className="pt-0.5">{step}</span>
                </li>
              ))}
            </ol>
          </Block>
        )}
      </div>

      {item.results && item.results.length > 0 && (
        <section className="grid gap-3" aria-label="Результаты" data-results>
          <h3 className="text-lg font-semibold">Результаты</h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {item.results.map((result) => (
              <figure key={result.title} className={`${CARD_CLASS} gap-2`} style={CARD_STYLE}>
                {result.image && <img src={result.image} alt={result.title} className="w-full rounded-lg" />}
                <figcaption className="grid gap-1 text-sm">
                  <span className="font-semibold">{result.title}</span>
                  {result.caption && <span className="text-[var(--neo-text-secondary)]">{result.caption}</span>}
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
