export type StatusPayload = {
  configured: boolean;
  persona?: Record<string, unknown>;
  model?: string;
  tools?: string[];
  scheduler_jobs?: Array<Record<string, unknown>>;
  memory_dir?: string;
};

export type ToolCall = {
  id?: string;
  name: string;
  arguments?: Record<string, unknown>;
  result?: string;
  source?: string;
};

export type SearchSource = {
  title: string;
  url: string;
  snippet?: string;
  date?: string;
};

export type Emotion = {
  label?: string;
  strength?: number;
  reason?: string;
  category?: string | null;
  intensity?: number;
};

export type EmotionalSupport = {
  mode?: string;
  need?: string;
  mirror?: string;
  small_action?: string;
  safety_note?: string;
};

export type PendingMessage = {
  id?: number;
  message_id?: number;
  role?: "assistant" | "system" | string;
  source: string;
  content: string;
  scheduled_at?: string;
  delivered_at?: string | null;
  meta?: Record<string, unknown>;
};

export type CbtPrompt = {
  technique?: string;
  title?: string;
  description?: string;
};

export type ChatResponse = {
  text?: string;
  turn_id?: string;
  steps?: number;
  finish_reason?: string;
  tool_calls?: ToolCall[];
  emotion?: Emotion | null;
  emotional_support?: EmotionalSupport | null;
  triggered_skills?: string[];
  search_sources?: SearchSource[];
  pending_messages?: PendingMessage[];
  cbt_prompt?: CbtPrompt | null;
  crisis_alert?: boolean;
};

export type ChatLogMessage = {
  id: number;
  session_id: string;
  role: "user" | "assistant" | "tool" | "system" | string;
  content: string;
  meta?: Record<string, unknown>;
  created_at?: string | null;
};

export type MessagesPayload = {
  messages: ChatLogMessage[];
};

/* ---- 心理健康新增类型 ---- */

export type MoodRecord = {
  id: number;
  date: string;
  score: number | null;
  notes?: string;
  category?: string;
  emotion_data?: Record<string, unknown>;
};

export type AssessmentDimensionStatus = {
  dimension_index: number;
  name: string;
  status: "unasked" | "asked" | "answered" | "scored";
  score?: number;
  source_conversation?: string | null;
  scored_at?: string | null;
};

export type AssessmentStatusResponse = {
  phq9: AssessmentDimensionStatus[];
  gad7: AssessmentDimensionStatus[];
  phq9_total?: number;
  gad7_total?: number;
  phq9_level?: string;
  gad7_level?: string;
};

export type CrisisResourcesResponse = {
  hotlines: Array<{
    title: string;
    phone: string;
    description?: string;
  }>;
};

export type AuthResponse = {
  user_id: number;
  username: string;
};

export type UserInfo = {
  user_id?: number;
  username?: string;
  display_name?: string;
};

export type MoodRecordsResponse = {
  records: MoodRecord[];
};

export type MoodTrendsResponse = {
  daily_averages: Array<{ date: string; avg_score: number }>;
};

export type MoodStatsResponse = {
  total_records: number;
  streak: number;
  categories: Record<string, number>;
  avg_score?: number | null;
  best_day?: string | null;
  worst_day?: string | null;
};

export type AssessmentCycle = {
  id: number;
  assessment_type: "phq9" | "gad7" | string;
  total_score: number;
  severity: string;
  started_at?: string | null;
  completed_at?: string | null;
};

export type AssessmentHistoryResponse = {
  cycles: AssessmentCycle[];
};
