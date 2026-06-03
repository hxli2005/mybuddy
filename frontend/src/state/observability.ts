import type { EmotionalSupport, Emotion, ToolCall } from "../types/api";

export type ViewId =
  | "chat"
  | "overview"
  | "memory"
  | "profile"
  | "reminders"
  | "skills"
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
  memory: ["memory"],
  reminders: ["reminders"],
  skills: ["skills"],
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
    summary: "校正长期记忆与原始记录",
    group: "context",
    shortcut: "3",
  },
  {
    id: "profile",
    label: "画像",
    summary: "维护核心字段与动态命题",
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
    id: "persona",
    label: "人格",
    summary: "编辑称呼、边界与回应习惯",
    group: "system",
    shortcut: "8",
  },
];

export const viewMetaById = Object.fromEntries(viewMetas.map((meta) => [meta.id, meta])) as Record<
  ViewId,
  ViewMeta
>;

export function isViewId(value: string): value is ViewId {
  return Object.prototype.hasOwnProperty.call(viewMetaById, value);
}
