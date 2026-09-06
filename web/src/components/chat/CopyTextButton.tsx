import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { copyTextToClipboard } from "@/lib/clipboard";

interface CopyTextButtonProps {
  text: string;
  label: string;
}

/** Копируем исходный текст: без подписей интерфейса и курсора потока. */
export function CopyTextButton({ text, label }: CopyTextButtonProps) {
  const [result, setResult] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (result === "idle") return;
    const timer = window.setTimeout(() => setResult("idle"), 2500);
    return () => window.clearTimeout(timer);
  }, [result]);

  return (
    <button
      type="button"
      className="korra-chat-copy"
      onClick={() => void copyTextToClipboard(text).then(ok => setResult(ok ? "copied" : "failed"))}
      aria-label={label}
    >
      {result === "copied" ? <Check size={15} aria-hidden /> : <Copy size={15} aria-hidden />}
      <span role="status">
        {result === "copied" ? "Скопировано" : result === "failed" ? "Не скопировалось · ещё раз" : label}
      </span>
    </button>
  );
}
