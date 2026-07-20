import { useState, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Shell } from "./components/Shell";
import { ChatView } from "./views/ChatView";
import { LoginView } from "./views/LoginView";
import { MoodDiary } from "./views/MoodDiary";
import { AssessmentStatus } from "./views/AssessmentStatus";
import { SafetyDisclaimer } from "./components/SafetyDisclaimer";
import { useAuth } from "./lib/auth";
import { useRouter } from "./lib/router";
import { queryKeys, type Presence } from "./lib/queryKeys";
import type { ChatResponse } from "./types/api";

export function App() {
  const queryClient = useQueryClient();
  const { loading } = useAuth();
  const { page } = useRouter();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [crisisOpen, setCrisisOpen] = useState(false);
  const [presence, setPresence] = useState<Presence>("calm");

  const handleOpenCrisis = useCallback(() => setCrisisOpen(true), []);

  function handleChatResult(response: ChatResponse) {
    const label = response.emotion?.label;
    setPresence(label === "positive" ? "positive" : label === "negative" ? "negative" : "calm");
    for (const key of [
      queryKeys.messages,
      queryKeys.status,
    ]) {
      queryClient.invalidateQueries({ queryKey: key });
    }
  }

  // 登录页：不需要 Shell 包裹
  if (page === "login") {
    return <div className="h-dvh overflow-hidden"><LoginView /></div>;
  }

  // 加载中：简单占位
  if (loading) {
    return (
      <div className="h-dvh flex items-center justify-center bg-bg overflow-hidden">
        <div className="text-muted text-[14px] animate-fade-in">正在连接…</div>
      </div>
    );
  }

  return (
    <div className="h-dvh flex flex-col overflow-hidden">
      <SafetyDisclaimer onOpenCrisis={handleOpenCrisis} />
      <Shell
        presence={presence}
        settingsOpen={settingsOpen}
        onOpenSettings={() => setSettingsOpen(true)}
        onCloseSettings={() => setSettingsOpen(false)}
        crisisOpen={crisisOpen}
        onOpenCrisis={handleOpenCrisis}
        onCloseCrisis={() => setCrisisOpen(false)}
      >
        {page === "chat" ? (
          <ChatView onChatResult={handleChatResult} onOpenCrisis={handleOpenCrisis} />
        ) : page === "mood" ? (
          <MoodDiary />
        ) : page === "assessment" ? (
          <AssessmentStatus />
        ) : (
          <ChatView onChatResult={handleChatResult} onOpenCrisis={handleOpenCrisis} />
        )}
      </Shell>
    </div>
  );
}

