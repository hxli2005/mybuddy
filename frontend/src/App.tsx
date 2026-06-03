import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ObserverPanel } from "./components/ObserverPanel";
import { Shell } from "./components/Shell";
import {
  emptyRuntime,
  isViewId,
  queryKeys,
  viewGroups,
  viewMetaById,
  viewMetas,
  type RuntimeSnapshot,
  type ViewId,
} from "./state/observability";
import type { ChatResponse } from "./types/api";
import { ChatView } from "./views/ChatView";
import { MemoryView } from "./views/MemoryView";
import { NotesView } from "./views/NotesView";
import { OverviewView } from "./views/OverviewView";
import { PersonaView } from "./views/PersonaView";
import { ProfileView } from "./views/ProfileView";
import { RemindersView } from "./views/RemindersView";
import { SkillsView } from "./views/SkillsView";

export function App() {
  const queryClient = useQueryClient();
  const [activeView, setActiveView] = useState<ViewId>(() => readInitialView());
  const [runtime, setRuntime] = useState<RuntimeSnapshot>(emptyRuntime);

  useEffect(() => {
    document.title = `${viewMetaById[activeView].label} · MyBuddy`;
  }, [activeView]);

  useEffect(() => {
    function syncFromHash() {
      const candidate = window.location.hash.replace("#", "");
      if (isViewId(candidate)) {
        setActiveView(candidate);
      }
    }

    window.addEventListener("hashchange", syncFromHash);
    return () => window.removeEventListener("hashchange", syncFromHash);
  }, []);

  const active = useMemo(() => {
    switch (activeView) {
      case "overview":
        return <OverviewView onNavigate={changeView} />;
      case "memory":
        return <MemoryView />;
      case "profile":
        return <ProfileView />;
      case "reminders":
        return <RemindersView />;
      case "skills":
        return <SkillsView />;
      case "notes":
        return <NotesView />;
      case "persona":
        return <PersonaView />;
      case "chat":
      default:
        return <ChatView onChatResult={handleChatResult} />;
    }
  }, [activeView]);

  function changeView(view: ViewId) {
    setActiveView(view);
    window.history.replaceState(null, "", `#${view}`);
  }

  function handleChatResult(response: ChatResponse) {
    setRuntime({
      turnId: response.turn_id || null,
      toolCalls: response.tool_calls || [],
      emotion: response.emotion || null,
      support: response.emotional_support || null,
    });
    for (const key of [
      queryKeys.status,
      queryKeys.profile,
      queryKeys.memory,
      queryKeys.reminders,
      queryKeys.skills,
      queryKeys.notes,
    ]) {
      queryClient.invalidateQueries({ queryKey: key });
    }
  }

  return (
    <Shell
      activeMeta={viewMetaById[activeView]}
      activeView={activeView}
      groups={viewGroups}
      inspector={<ObserverPanel runtime={runtime} />}
      onViewChange={changeView}
      runtime={runtime}
      views={viewMetas}
    >
      {active}
    </Shell>
  );
}

function readInitialView(): ViewId {
  if (typeof window === "undefined") return "chat";
  const candidate = window.location.hash.replace("#", "");
  return isViewId(candidate) ? candidate : "chat";
}
