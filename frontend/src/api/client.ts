import type {
  ChatResponse,
  MessagesPayload,
  MemoryPayload,
  MemoryItem,
  Note,
  NotesPayload,
  Persona,
  PersonaPayload,
  ProfileClaim,
  ProfilePayload,
  Reminder,
  RemindersPayload,
  Skill,
  SkillsPayload,
  StatusPayload,
  TestUser,
  UserPersonaPayload,
  UsersPayload,
} from "../types/api";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return data as T;
}

export function fetchStatus(): Promise<StatusPayload> {
  return request<StatusPayload>("/api/status");
}

export function fetchPersona(): Promise<PersonaPayload> {
  return request<PersonaPayload>("/api/persona");
}

export function savePersona(persona: Persona): Promise<PersonaPayload> {
  return request<PersonaPayload>("/api/persona", {
    method: "PUT",
    body: JSON.stringify(persona),
  });
}

export function fetchProfile(): Promise<ProfilePayload> {
  return request<ProfilePayload>("/api/profile");
}

export function updateProfileField(key: string, value: string): Promise<{ field: { key: string; value: string } }> {
  return request<{ field: { key: string; value: string } }>(`/api/profile/fields/${encodeURIComponent(key)}`, {
    method: "PATCH",
    body: JSON.stringify({ value }),
  });
}

export function deleteProfileField(key: string): Promise<{ ok: boolean; key: string }> {
  return request<{ ok: boolean; key: string }>(`/api/profile/fields/${encodeURIComponent(key)}`, {
    method: "DELETE",
  });
}

export function updateProfileClaim(
  id: number,
  input: { claim?: string; confidence?: number },
): Promise<{ claim: ProfileClaim }> {
  return request<{ claim: ProfileClaim }>(`/api/profile/claims/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteProfileClaim(id: number): Promise<{ ok: boolean; id: number }> {
  return request<{ ok: boolean; id: number }>(`/api/profile/claims/${id}`, {
    method: "DELETE",
  });
}

export function fetchMemory(): Promise<MemoryPayload> {
  return request<MemoryPayload>("/api/memory");
}

export function updateMemoryItem(id: string, input: { content?: string }): Promise<{ memory: MemoryItem }> {
  return request<{ memory: MemoryItem }>(`/api/memory/archive/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteMemoryItem(id: string): Promise<{ ok: boolean; id: string }> {
  return request<{ ok: boolean; id: string }>(`/api/memory/archive/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function fetchReminders(): Promise<RemindersPayload> {
  return request<RemindersPayload>("/api/reminders");
}

export function cancelReminder(id: number): Promise<{ reminder: Reminder }> {
  return request<{ reminder: Reminder }>(`/api/reminders/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status: "cancelled" }),
  });
}

export function fetchSkills(): Promise<SkillsPayload> {
  return request<SkillsPayload>("/api/skills");
}

export function updateSkill(name: string, archived: boolean): Promise<{ skill: Skill }> {
  return request<{ skill: Skill }>(`/api/skills/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: JSON.stringify({ archived }),
  });
}

export function fetchNotes(): Promise<NotesPayload> {
  return request<NotesPayload>("/api/notes");
}

export function createNote(input: {
  title?: string;
  content: string;
  tags?: string[];
}): Promise<{ note: Note }> {
  return request<{ note: Note }>("/api/notes", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateNote(
  id: number,
  input: { title?: string; content?: string; tags?: string[] },
): Promise<{ note: Note }> {
  return request<{ note: Note }>(`/api/notes/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteNote(id: number): Promise<{ ok: boolean; id: number }> {
  return request<{ ok: boolean; id: number }>(`/api/notes/${id}`, {
    method: "DELETE",
  });
}

export function fetchUsers(): Promise<UsersPayload> {
  return request<UsersPayload>("/api/users");
}

export function createUser(input: {
  display_name: string;
  daily_message_limit?: number;
}): Promise<{ user: TestUser }> {
  return request<{ user: TestUser }>("/api/users", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateUser(
  id: number,
  input: { status?: string; daily_message_limit?: number },
): Promise<{ user: TestUser }> {
  return request<{ user: TestUser }>(`/api/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function bindQqAccount(
  userId: number,
  input: { external_id: string; display_name?: string },
): Promise<{ user: TestUser }> {
  return request<{ user: TestUser }>(`/api/users/${userId}/qq`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function fetchUserPersona(userId: number): Promise<UserPersonaPayload> {
  return request<UserPersonaPayload>(`/api/users/${userId}/persona`);
}

export function saveUserPersona(userId: number, persona: Persona): Promise<UserPersonaPayload> {
  return request<UserPersonaPayload>(`/api/users/${userId}/persona`, {
    method: "PUT",
    body: JSON.stringify(persona),
  });
}

export function resetUserPersona(userId: number): Promise<UserPersonaPayload> {
  return request<UserPersonaPayload>(`/api/users/${userId}/persona`, {
    method: "DELETE",
  });
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

export function sendFeedback(label: string, turnId: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/api/feedback", {
    method: "POST",
    body: JSON.stringify({ label, turn_id: turnId }),
  });
}
