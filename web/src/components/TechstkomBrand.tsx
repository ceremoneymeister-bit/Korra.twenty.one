import { withBasePath } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Approved PNG stays intact; the frame trims its transparent top/bottom margins. */
export function TechstkomBrand({ themeName }: TechstkomBrandProps) {
  return (
    <span className="relative block h-10 w-[172px] shrink-0 overflow-hidden">
      <img
        src={withBasePath("/techstkom-wordmark-v1.png")}
        alt="Техстком"
        width={2172}
        height={724}
        className={cn(
          "absolute top-1/2 left-0 h-auto w-full -translate-y-1/2",
          themeName === "dark" && "brightness-0 invert",
        )}
      />
    </span>
  );
}

interface TechstkomBrandProps {
  themeName: string;
}
