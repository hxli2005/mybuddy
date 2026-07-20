export const queryKeys = {
  status: ["status"],
  messages: ["messages"],
  mood: ["mood-records"],
  moodStats: ["mood-stats"],
  assessment: ["assessment-status"],
  assessmentHistory: ["assessment-history"],
} as const;

export type Presence = "calm" | "positive" | "negative";
