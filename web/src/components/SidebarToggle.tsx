import { ChevronLeft, Menu } from "lucide-react";
import { cn } from "@/lib/utils";

/** The 44 px target and centred surface are shared by drawer and desktop. */
export function SidebarToggle({ expanded, label, onClick, className }: {
  expanded: boolean;
  label: string;
  onClick: () => void;
  className?: string;
}) {
  const Icon = expanded ? ChevronLeft : Menu;
  return (
    <button
      type="button"
      aria-label={label}
      aria-expanded={expanded}
      aria-controls="app-sidebar"
      title={label}
      data-sidebar-toggle
      onClick={onClick}
      className={cn("group/sidebar-toggle grid size-[44px] shrink-0 place-items-center text-[var(--neo-text-secondary)]", className)}
      style={{ borderRadius: "50%" }}
    >
      <span className="grid size-[32px] place-items-center rounded-full bg-[var(--neo-surface)] shadow-[var(--neo-depth-1)] transition-[box-shadow,color] group-hover/sidebar-toggle:text-[var(--neo-text-primary)] group-hover/sidebar-toggle:shadow-[var(--neo-inset-compact)] group-active/sidebar-toggle:shadow-[var(--neo-inset-compact)] pointer-coarse:size-[36px]">
        <Icon aria-hidden className="size-[18px]" strokeWidth={1.75} />
      </span>
    </button>
  );
}
