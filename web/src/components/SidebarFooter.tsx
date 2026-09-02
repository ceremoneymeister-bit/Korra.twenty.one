import { Typography } from "@nous-research/ui/ui/components/typography/index";
import type { StatusResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

export function SidebarFooter({ status }: SidebarFooterProps) {
  return (
    <div
      className={cn(
        "flex shrink-0 items-center",
        "px-5 py-2.5",
        "border-t border-current/10",
      )}
    >
      <Typography
        className="font-mono-ui text-xs tabular-nums tracking-[0.08em] text-text-tertiary lowercase"
      >
        {status?.version != null ? `v${status.version}` : "—"}
      </Typography>
    </div>
  );
}

interface SidebarFooterProps {
  status: StatusResponse | null;
}
