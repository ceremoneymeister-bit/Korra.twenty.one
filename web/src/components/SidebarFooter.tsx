import { Typography } from "@nous-research/ui/ui/components/typography/index";
import type { StatusResponse } from "@/lib/api";
import { cn } from "@/lib/utils";
import { KorraBrand } from "@/components/KorraBrand";

export function SidebarFooter({ status, poweredByTheme }: SidebarFooterProps) {
  if (poweredByTheme != null) {
    return (
      <div className="flex shrink-0 items-center gap-2 px-5 py-3">
        <span className="text-xs text-text-secondary">powered by</span>
        <KorraBrand themeName={poweredByTheme} className="h-3 w-auto max-w-[70px]" />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex shrink-0 items-center",
        "px-5 py-2.5",
      )}
    >
      <Typography
        className="font-sans text-xs tabular-nums tracking-[0.08em] text-text-tertiary lowercase"
      >
        {status?.version != null ? `v${status.version}` : "—"}
      </Typography>
    </div>
  );
}

interface SidebarFooterProps {
  status: StatusResponse | null;
  poweredByTheme?: string;
}
