export const queryKeys = {
  status: ["status"],
  persona: ["persona"],
  profile: ["profile"],
  messages: ["messages"],
  memory: ["memory"],
  reminders: ["reminders"],
  skills: ["skills"],
  notes: ["notes"],
} as const;

export type Presence = "calm" | "positive" | "negative";
