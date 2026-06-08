import type { EmotionalSupport, Emotion, ToolCall } from "../types/api";

export type ViewId =
  | "chat"
  | "overview"
  | "memory"
  | "profile"
  | "reminders"
  | "skills"
  | "users"
  | "notes"
  | "persona";

export type ViewGroupId = "session" | "context" | "operations" | "system";

export type ViewGroup = {
  id: ViewGroupId;
  label: string;
  summary: string;
};

export type ViewMeta = {
  id: ViewId;
  label: string;
  summary: string;
  group: ViewGroupId;
  shortcut: string;
};

export type RuntimeSnapshot = {
  turnId: string | null;
  toolCalls: ToolCall[];
  emotion: Emotion | null;
  support: EmotionalSupport | null;
};

export const emptyRuntime: RuntimeSnapshot = {
  turnId: null,
  toolCalls: [],
  emotion: null,
  support: null,
};

export const queryKeys = {
  status: ["status"],
  persona: ["persona"],
  profile: ["profile"],
  messages: ["messages"],
  memory: ["memory"],
  reminders: ["reminders"],
  skills: ["skills"],
  users: ["users"],
  userPersona: (userId: number) => ["users", userId, "persona"] as const,
  notes: ["notes"],
} as const;

export const viewGroups: ViewGroup[] = [
  { id: "session", label: "会话", summary: "当下输入、回复、反馈" },
  { id: "context", label: "上下文", summary: "记忆、画像、笔记" },
  { id: "operations", label: "运行", summary: "提醒、Skills、状态" },
  { id: "system", label: "配置", summary: "人格与回应规则" },
];

export const viewMetas: ViewMeta[] = [
  {
    id: "chat",
    label: "对话",
    summary: "发送消息，查看回复与待播信息",
    group: "session",
    shortcut: "1",
  },
  {
    id: "overview",
    label: "总览",
    summary: "按任务优先级查看系统全貌",
    group: "session",
    shortcut: "2",
  },
  {
    id: "memory",
    label: "记忆",
    summary: "校正少量长期记忆",
    group: "context",
    shortcut: "3",
  },
  {
    id: "profile",
    label: "画像",
    summary: "维护明确字段与候选观察",
    group: "context",
    shortcut: "4",
  },
  {
    id: "notes",
    label: "笔记",
    summary: "记录材料并写入可检索上下文",
    group: "context",
    shortcut: "5",
  },
  {
    id: "reminders",
    label: "提醒",
    summary: "管理定时提醒和待播消息",
    group: "operations",
    shortcut: "6",
  },
  {
    id: "skills",
    label: "Skills",
    summary: "审查技能触发和归档状态",
    group: "operations",
    shortcut: "7",
  },
  {
    id: "users",
    label: "测试用户",
    summary: "管理测试账号、QQ 绑定和额度",
    group: "operations",
    shortcut: "8",
  },
  {
    id: "persona",
    label: "人格",
    summary: "编辑称呼、边界与回应习惯",
    group: "system",
    shortcut: "9",
  },
];

export const viewMetaById = Object.fromEntries(viewMetas.map((meta) => [meta.id, meta])) as Record<
  ViewId,
  ViewMeta
>;

export function isViewId(value: string): value is ViewId {
  return Object.prototype.hasOwnProperty.call(viewMetaById, value);
}
