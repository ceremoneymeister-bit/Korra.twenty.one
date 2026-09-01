import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface ProductButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "prefix"> {
  prefix?: ReactNode;
  suffix?: ReactNode;
  outlined?: boolean;
  ghost?: boolean;
  destructive?: boolean;
  size?: "default" | "sm" | "xs" | "icon";
}

/** Calm, readable action button for the owner-facing product surfaces. */
export function ProductButton({
  children,
  className,
  prefix,
  suffix,
  outlined,
  ghost,
  destructive,
  size = "default",
  type = "button",
  ...props
}: ProductButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg border font-sans font-semibold normal-case tracking-normal outline-none transition-colors duration-150",
        "focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background-base",
        "disabled:pointer-events-none disabled:opacity-50",
        ghost
          ? "border-transparent bg-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground"
          : destructive
            ? outlined
              ? "border-destructive/40 bg-transparent text-destructive hover:bg-destructive/10"
              : "border-destructive bg-destructive text-destructive-foreground hover:bg-destructive/90"
            : outlined
              ? "border-border bg-background/55 text-foreground hover:border-primary/45 hover:bg-primary/[0.06]"
              : "border-primary bg-primary text-primary-foreground hover:bg-primary/90",
        size === "icon" && "size-[44px] shrink-0 p-0 [&>svg]:size-4",
        size === "xs" && "min-h-[44px] px-2.5 py-1.5 text-xs [&>svg]:size-3.5",
        size === "sm" && "min-h-[44px] px-3.5 py-2 text-sm [&>svg]:size-4",
        size === "default" && "min-h-[44px] px-4 py-2.5 text-sm [&>svg]:size-4",
        className,
      )}
      {...props}
    >
      {prefix}
      {children}
      {suffix}
    </button>
  );
}

export { ProductButton as Button };
