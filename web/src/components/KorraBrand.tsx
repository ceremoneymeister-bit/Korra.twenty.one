import { cn } from "@/lib/utils";
import { withBasePath } from "@/lib/api";

/** Theme-aware Korra wordmark for the dashboard header and sidebar.
 *
 * Korra: знак «21» рядом со словесным знаком снят по решению владельца
 * 03.09.2026 — в шапке остаётся только KORRA. Ассет `/korra-21.png` остаётся
 * в public/ и ждёт своего места (пустые состояния, экран «о продукте»).
 */
export function KorraBrand({ className, themeName }: KorraBrandProps) {
  const src = themeName === "dark"
    ? withBasePath("/korra-logo-dark.png?v=0.21.14")
    : withBasePath("/korra-logo-light.png?v=0.21.14");

  return (
    <img
      src={src}
      alt="Korra"
      className={cn(
        "h-[26px] w-auto max-w-[132px] shrink-0 object-contain",
        className,
      )}
    />
  );
}

interface KorraBrandProps {
  className?: string;
  themeName: string;
}
