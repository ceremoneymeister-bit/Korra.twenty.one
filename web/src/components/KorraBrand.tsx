import { cn } from "@/lib/utils";
import { withBasePath } from "@/lib/api";

/** Theme-aware Korra 21 brand lockup for the dashboard header and sidebar. */
export function KorraBrand({ className, themeName }: KorraBrandProps) {
  const src = themeName === "dark"
    ? withBasePath("/korra-logo-dark.png")
    : withBasePath("/korra-logo-light.png");

  return (
    <span
      role="img"
      aria-label="Korra 21"
      className={cn(
        "inline-flex h-[22px] shrink-0 items-center gap-[7px]",
        className,
      )}
    >
      <img
        src={src}
        alt=""
        aria-hidden="true"
        className="h-full w-auto max-w-[132px] shrink-0 object-contain"
      />
      <img
        src={withBasePath("/korra-21.png")}
        alt=""
        aria-hidden="true"
        className="h-full w-auto shrink-0 object-contain"
      />
    </span>
  );
}

interface KorraBrandProps {
  className?: string;
  themeName: string;
}
