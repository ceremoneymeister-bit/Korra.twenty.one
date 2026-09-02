import { cn } from "@/lib/utils";
import { withBasePath } from "@/lib/api";

/** Theme-aware Korra wordmark for the dashboard header and sidebar. */
export function KorraBrand({ className, themeName }: KorraBrandProps) {
  const src = themeName === "dark"
    ? withBasePath("/korra-logo-dark.png")
    : withBasePath("/korra-logo-light.png");

  return (
    <img
      src={src}
      alt="Korra"
      className={cn(
        "h-[22px] w-auto max-w-[132px] shrink-0 object-contain",
        className,
      )}
    />
  );
}

interface KorraBrandProps {
  className?: string;
  themeName: string;
}
