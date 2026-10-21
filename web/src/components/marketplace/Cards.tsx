import { Link } from "react-router";
import {
  hasOwnPage,
  orderLabel,
  type MarketplaceItem,
  type MarketplaceSection,
} from "@/lib/marketplace-catalog";
import { cn } from "@/lib/utils";
import { CARD_CLASS, CARD_STYLE, IconTile, ItemOrderButton, Price, iconFor } from "./ui";

/** Платная позиция: название ведёт на её страницу, кнопка — сразу к заказу. */
export function CatalogItemCard({ item, section }: { item: MarketplaceItem; section: MarketplaceSection }) {
  const label = orderLabel(item, section);
  const ownPage = hasOwnPage(item);
  return (
    <article
      className={cn(CARD_CLASS, ownPage && "transition-shadow hover:shadow-[var(--neo-depth-2)] focus-within:shadow-[var(--neo-depth-2)]")}
      style={CARD_STYLE}
      data-item={item.id}
    >
      <IconTile icon={iconFor(item.icon)} />
      <div className="grid gap-1">
        <h3 className="text-base font-semibold">
          {ownPage ? (
            <Link
              to={`/marketplace/${section.id}/${item.id}`}
              className="outline-none focus-visible:shadow-none! after:absolute after:inset-0 after:rounded-[var(--neo-radius-control)] focus-visible:after:ring-2 focus-visible:after:ring-primary/50"
            >
              {item.name}
            </Link>
          ) : item.name}
        </h3>
        <p className="text-sm leading-relaxed text-[var(--neo-text-secondary)]">{item.tagline}</p>
      </div>
      <div className="mt-auto grid gap-3 pt-1">
        {item.price && <Price price={item.price} note={item.price_note} />}
        {label && <ItemOrderButton item={item} label={label} />}
      </div>
    </article>
  );
}

/** Пример из практики: без цены и кнопки — заказ общий у раздела. */
export function ExampleCard({ item }: { item: MarketplaceItem }) {
  return (
    <article className={CARD_CLASS} style={CARD_STYLE} data-example={item.id}>
      <IconTile icon={iconFor(item.icon)} />
      <div className="grid gap-1">
        {item.industry && <p className="text-xs font-medium text-[var(--neo-text-secondary)]">{item.industry}</p>}
        <h3 className="text-base font-semibold">{item.name}</h3>
        <p className="text-sm leading-relaxed text-[var(--neo-text-secondary)]">{item.tagline}</p>
      </div>
    </article>
  );
}
