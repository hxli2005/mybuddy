export type GuestMessage = { role: "user" | "assistant"; content: string };

const GUEST_KEY = "mybuddy-guest-messages";
const MAX_MESSAGES = 50;

export function loadGuestMessages(): GuestMessage[] {
  try {
    const raw = localStorage.getItem(GUEST_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (m): m is GuestMessage =>
          Boolean(m) &&
          (m.role === "user" || m.role === "assistant") &&
          typeof m.content === "string" &&
          m.content.trim().length > 0,
      )
      .slice(-MAX_MESSAGES);
  } catch {
    return [];
  }
}

export function saveGuestMessages(messages: GuestMessage[]) {
  try {
    localStorage.setItem(GUEST_KEY, JSON.stringify(messages.slice(-MAX_MESSAGES)));
  } catch {
    /* 存储满/隐私模式:静默失败 */
  }
}

export function clearGuestMessages() {
  try {
    localStorage.removeItem(GUEST_KEY);
  } catch {
    /* ignore */
  }
}
