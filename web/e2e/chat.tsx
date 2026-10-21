// Loopback Playwright fixture: real history hook, transcript and composer.
import "../src/index.css";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { BubbleChatComposer, BubbleChatTranscript } from "../src/pages/BubbleChatPage";
import { useChatStream } from "../src/hooks/useChatStream";
import { EffectDecisionCenter } from "../src/components/chat/EffectDecisionCenter";

function Chat() {
  const chat = useChatStream({ profile: "default" });
  const decisions = new URLSearchParams(location.search).has("decisions");
  return <MemoryRouter>
    <output id="loading">{String(chat.isLoading)}</output>
    <output id="count">{chat.messages.length}</output>
    {decisions && <EffectDecisionCenter profile="default" currentSessionId={chat.sessionId} onCurrentDecision={chat.resolveApproval} />}
    <div style={{ height: decisions ? 580 : 400, display: "flex", flexDirection: "column" }}>
      <BubbleChatTranscript messages={chat.messages} sessionId={chat.sessionId} scrollKey="e2e:scroll" older={chat.older} onLoadOlder={() => void chat.loadOlder()}
        approvals={decisions ? chat.approvals : undefined} onApprovalDecision={chat.resolveApproval} />
    </div>
    <BubbleChatComposer draftKey="e2e" profile="default" disabled={chat.isLoading} onSend={chat.send} allowAttachments />
  </MemoryRouter>;
}
createRoot(document.getElementById("root")!).render(<Chat />);
