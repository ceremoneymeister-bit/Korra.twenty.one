import "../src/index.css";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useNavigate } from "react-router";
import AgentWorkbenchPage from "../src/pages/AgentWorkbenchPage";

function Handoff() {
  const navigate = useNavigate();
  return <div style={{ display: "flex", height: "100vh", width: "100%", minWidth: 0 }}>
    <div style={{ flex: 1, minWidth: 0 }}>
      <button onClick={() => navigate("/agents?agent=designer&resume=recent-chat&attach=%2Fsynthetic%2Fbrief.txt")}>Передать в недавний</button>
      <button onClick={() => navigate("/agents?agent=designer&new_chat=1&attach=%2Fsynthetic%2Fbrief.txt")}>Передать в новый</button>
      <AgentWorkbenchPage />
    </div>
  </div>;
}
createRoot(document.getElementById("root")!).render(<MemoryRouter initialEntries={["/agents?agent=designer"]}><Handoff /></MemoryRouter>);
