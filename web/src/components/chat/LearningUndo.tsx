import { useState } from "react";
import { Button } from "@nous-research/ui/ui/components/button";

import { api } from "@/lib/api";

/** «Отменить» под сообщением «Учёл…»: откатывает именно то изменение, о котором оно сообщило. */
export function LearningUndo({
  sessionId,
  messageId,
  profile,
  undone,
}: {
  sessionId: string;
  messageId: number;
  profile: string;
  undone: boolean;
}) {
  const [state, setState] = useState<{ busy: boolean; undone: boolean; note: string }>({
    busy: false,
    undone,
    note: "",
  });

  const onUndo = async () => {
    setState((current) => ({ ...current, busy: true, note: "" }));
    try {
      const result = await api.undoLearningNotice(sessionId, messageId, profile);
      setState({
        busy: false,
        undone: result.status === "undone" || result.status === "already_undone",
        note: result.message,
      });
    } catch {
      setState({ busy: false, undone: false, note: "Не удалось отменить. Попробуйте ещё раз." });
    }
  };

  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
      {state.undone ? (
        <span role="status">{state.note || "Отменено"}</span>
      ) : (
        <Button ghost size="sm" disabled={state.busy} onClick={() => void onUndo()}>
          Отменить
        </Button>
      )}
      {!state.undone && state.note && <span role="status">{state.note}</span>}
    </div>
  );
}
