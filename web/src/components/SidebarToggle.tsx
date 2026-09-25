import { ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

/** The 44 px target and centred surface are shared by drawer and desktop. */
export function SidebarToggle({ expanded, label, onClick, className }: {
  expanded: boolean;
  label: string;
  onClick: () => void;
  className?: string;
}) {
  const [pressed, setPressed] = useState(false);
  const Icon = expanded ? ChevronLeft : ChevronRight;
  return (
    <button
      type="button"
      aria-label={label}
      aria-expanded={expanded}
      aria-controls="app-sidebar"
      title={label}
      data-sidebar-toggle
      data-pressed={pressed || undefined}
      onPointerDown={() => setPressed(true)}
      onPointerUp={() => setPressed(false)}
      onPointerCancel={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      onLostPointerCapture={() => setPressed(false)}
      onBlur={() => setPressed(false)}
      onClick={onClick}
      className={cn("grid size-[44px] shrink-0 place-items-center text-[var(--neo-text-secondary)]", className)}
      style={{ borderRadius: "50%" }}
    >
      <span className="sidebar-toggle__surface grid size-[32px] place-items-center rounded-full pointer-coarse:size-[36px]">
        <Icon aria-hidden className="size-[18px]" strokeWidth={1.75} />
      </span>
    </button>
  );
}
