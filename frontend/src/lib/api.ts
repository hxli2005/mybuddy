import type {
  AssessmentHistoryResponse,
  AssessmentStatusResponse,
  AuthResponse,
  ChatResponse,
  CrisisResourcesResponse,
  MessagesPayload,
  MoodRecordsResponse,
  MoodStatsResponse,
  MoodTrendsResponse,
  StatusPayload,
  UserInfo,
} from "../types/api";

let onUnauthorized: (() => void) | null = null;

/** 注册全局 401 处理(AuthProvider 挂载时调用):清空登录态并跳转登录页。 */
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (res.status === 401 && onUnauthorized) {
    onUnauthorized();
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return data as T;
}

/* ---- 基础 ---- */

export function fetchStatus(): Promise<StatusPayload> {
  return request<StatusPayload>("/api/status");
}

export function sendChat(message: string): Promise<ChatResponse> {
  return request<ChatResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export function fetchMessages(limit = 100): Promise<MessagesPayload> {
  return request<MessagesPayload>(`/api/messages?limit=${limit}`);
}

export function resetChatContext(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/api/chat/reset", { method: "POST" });
}

export function sendFeedback(label: string, turnId: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/api/feedback", {
    method: "POST",
    body: JSON.stringify({ label, turn_id: turnId }),
  });
}

/* ---- 认证 ---- */

export function register(username: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function login(username: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function logout(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
}

export function fetchCurrentUser(): Promise<UserInfo> {
  return request<UserInfo>("/api/auth/me");
}

export function deleteAccount(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/api/auth/account", { method: "DELETE" });
}

/* ---- 情绪 ---- */

export function fetchMoodRecords(limit = 30): Promise<MoodRecordsResponse> {
  return request<MoodRecordsResponse>(`/api/mood?limit=${limit}`);
}

export function fetchMoodTrends(days = 30): Promise<MoodTrendsResponse> {
  return request<MoodTrendsResponse>(`/api/mood/trends?days=${days}`);
}

export function fetchMoodStats(): Promise<MoodStatsResponse> {
  return request<MoodStatsResponse>("/api/mood/stats");
}

export function moodCheckin(moodScore: number, notes?: string): Promise<{ ok: boolean; id: number }> {
  return request<{ ok: boolean; id: number }>("/api/mood/checkin", {
    method: "POST",
    body: JSON.stringify({ mood_score: moodScore, notes: notes || null }),
  });
}

/* ---- 评估 ---- */

export function fetchAssessmentStatus(): Promise<AssessmentStatusResponse> {
  return request<AssessmentStatusResponse>("/api/assessment/status");
}

export function fetchAssessmentHistory(): Promise<AssessmentHistoryResponse> {
  return request<AssessmentHistoryResponse>("/api/assessment/history");
}

export function resetAssessment(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/api/assessment/status", { method: "DELETE" });
}

/* ---- 安全 ---- */

export function fetchSafetyResources(): Promise<CrisisResourcesResponse> {
  return request<CrisisResourcesResponse>("/api/safety/resources");
}

/* ---- CBT ---- */

export function fetchCbtStatus(): Promise<{ events: Array<Record<string, unknown>> }> {
  return request<{ events: Array<Record<string, unknown>> }>("/api/cbt/status");
}

/* ---- 语音转文字 ---- */

export async function transcribeAudio(blob: Blob): Promise<string> {
  const res = await fetch("/api/transcribe", {
    method: "POST",
    credentials: "same-origin",
    body: blob,
  });
  if (res.status === 401 && onUnauthorized) {
    onUnauthorized();
  }
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return data.text || "";
}

/* ---- 用户数据 ---- */

export function importGuestMessages(
  messages: Array<{ role: string; content: string }>,
): Promise<{ ok: boolean; imported: number }> {
  return request<{ ok: boolean; imported: number }>("/api/messages/import", {
    method: "POST",
    body: JSON.stringify({ messages }),
  });
}

export function exportUserData(): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>("/api/user/export");
}

export function clearUserData(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/api/user/data", { method: "DELETE" });
}
