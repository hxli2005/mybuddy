export type Persona = {
  name?: string;
  style?: string;
  language?: string;
  relationship?: string;
  tone?: string;
  boundaries?: string;
  response_habits?: string[];
  roleplay_style?: Record<string, unknown>;
  character_life?: Record<string, unknown>;
  relationship_model?: Record<string, unknown>;
  address_user?: string;
};

export type StatusPayload = {
  configured: boolean;
  persona?: Persona;
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
  source: string;
  content: string;
  scheduled_at?: string;
  delivered_at?: string | null;
  meta?: Record<string, unknown>;
};

export type ChatResponse = {
  text?: string;
  turn_id?: string;
  steps?: number;
  finish_reason?: string;
  tool_calls?: ToolCall[];
  emotion?: Emotion | null;
  emotional_support?: EmotionalSupport | null;
  related_claim_ids?: number[];
  triggered_skills?: string[];
  search_sources?: SearchSource[];
  pending_messages?: PendingMessage[];
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

export type ProfileClaim = {
  sql_id?: number;
  claim: string;
  confidence: number;
  evidence_ids?: string[];
  status?: string;
  category?: string;
  evidence_count?: number;
  evidence_days?: string[];
  first_seen_at?: string;
  last_seen_at?: string;
  promoted_memory_id?: string | null;
  updated_at?: string;
};

export type ProfilePayload = {
  fields: Record<string, string>;
  claims: ProfileClaim[];
};

export type MemoryItem = {
  id: string;
  content: string;
  metadata?: Record<string, unknown>;
  score?: number;
};

export type MemoryPayload = {
  archive: MemoryItem[];
  conversations: Array<Record<string, unknown>>;
  raw: Array<Record<string, unknown>>;
};

export type Reminder = {
  id: number;
  content: string;
  trigger_at: string;
  status: string;
};

export type RemindersPayload = {
  reminders: Reminder[];
  pending_messages: PendingMessage[];
};

export type Skill = {
  name: string;
  triggers: string[];
  confidence: number;
  success_count: number;
  fail_count: number;
  archived: boolean;
};

export type SkillsPayload = {
  skills: Skill[];
};

export type Note = {
  id: number;
  title: string;
  content: string;
  tags: string[];
  created_at: string;
  updated_at: string;
};

export type NotesPayload = {
  notes: Note[];
};

export type PersonaPayload = {
  persona: Persona;
};
