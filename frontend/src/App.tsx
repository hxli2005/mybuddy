import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Shell } from "./components/Shell";
import { ChatView } from "./views/ChatView";
import { queryKeys, type Presence } from "./lib/queryKeys";
import type { ChatResponse } from "./types/api";

export function App() {
  const queryClient = useQueryClient();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [presence, setPresence] = useState<Presence>("calm");

  function handleChatResult(response: ChatResponse) {
    const label = response.emotion?.label;
    setPresence(label === "positive" ? "positive" : label === "negative" ? "negative" : "calm");
    for (const key of [
      queryKeys.messages,
      queryKeys.profile,
      queryKeys.memory,
      queryKeys.reminders,
      queryKeys.skills,
      queryKeys.notes,
      queryKeys.persona,
      queryKeys.status,
    ]) {
      queryClient.invalidateQueries({ queryKey: key });
    }
  }

  return (
    <Shell
      presence={presence}
      settingsOpen={settingsOpen}
      onOpenSettings={() => setSettingsOpen(true)}
      onCloseSettings={() => setSettingsOpen(false)}
    >
      <ChatView onChatResult={handleChatResult} />
    </Shell>
  );
}
