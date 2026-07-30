import { ListTree } from "lucide-react";
import type { Emotion, ToolCall } from "../types/api";

type CbtPromptData = {
  technique?: string;
  title?: string;
  description?: string;
};

type ChatPipelinePanelProps = {
  crisisAlert?: boolean;
  emotion?: Emotion | null;
  cbtPrompt?: CbtPromptData | null;
  triggeredSkills?: string[];
  toolCalls?: ToolCall[];
  steps?: number | null;
  finishReason?: string | null;
  sourceCount?: number;
};

/**
 * 系统决策透视面板:逐行展示本轮确定性管线的决策日志
 * (安全门/情绪/CBT/技能/工具与循环/检索决策)。
 * 展示的是结构化决策记录,不是模型生成的内心独白;空字段行不显示。
 * 样式对齐 ChatView 里的 SupportReveal 折叠块。
 */
export function ChatPipelinePanel({
  crisisAlert,
  emotion,
  cbtPrompt,
  triggeredSkills,
  toolCalls,
  steps,
  finishReason,
  sourceCount,
}: ChatPipelinePanelProps) {
  const rows: Array<[string, string]> = [];

  // 1. 安全门:硬底线命中情况
  if (typeof crisisAlert === "boolean") {
    rows.push([
      "安全门",
      crisisAlert ? "命中高危硬底线,已直接返回危机响应" : "未命中硬底线,正常放行",
    ]);
  }

  // 2. 情绪:label / category / intensity / strength / 判定依据
  if (emotion) {
    const parts: string[] = [];
    if (emotion.label) parts.push(emotion.label);
    if (emotion.category) parts.push(`类别 ${emotion.category}`);
    if (typeof emotion.intensity === "number") parts.push(`强度 ${emotion.intensity}`);
    if (typeof emotion.strength === "number") parts.push(`strength ${emotion.strength}`);
    if (emotion.reason) parts.push(`依据:${emotion.reason}`);
    if (parts.length) rows.push(["情绪", parts.join(" · ")]);
  }

  // 3. CBT:机会窗口命中技巧
  if (cbtPrompt) {
    const parts: string[] = [];
    if (cbtPrompt.title) parts.push(cbtPrompt.title);
    if (cbtPrompt.technique) parts.push(`技巧 ${cbtPrompt.technique}`);
    if (parts.length) rows.push(["CBT", `机会窗口命中:${parts.join(" · ")}`]);
  }

  // 4. 技能:triggered_skills
  if (triggeredSkills && triggeredSkills.length) {
    rows.push(["技能", triggeredSkills.join(" · ")]);
  }

  // 5. 工具与循环:工具调用摘要 + ReAct 步数 + finish_reason
  //    (带 decision_level 的检索预取调用归入第 6 行,避免重复)
  const searchCall = (toolCalls || []).find((c) => c.decision_level);
  const plainCalls = (toolCalls || []).filter((c) => c !== searchCall);
  const loopParts: string[] = [];
  if (plainCalls.length) loopParts.push(plainCalls.map(describeToolCall).join("；"));
  if (typeof steps === "number") loopParts.push(`ReAct ${steps} 步`);
  if (finishReason) loopParts.push(`结束原因 ${finishReason}`);
  if (loopParts.length) rows.push(["工具与循环", loopParts.join(" · ")]);

  // 6. 检索决策:must/should 判级与理由、来源数
  const searchParts: string[] = [];
  if (searchCall) {
    if (searchCall.decision_level) searchParts.push(`判级 ${searchCall.decision_level}`);
    if (searchCall.decision_reason) searchParts.push(searchCall.decision_reason);
    if (searchCall.decision_topic) searchParts.push(`兴趣话题 ${searchCall.decision_topic}`);
    if (typeof searchCall.result_count === "number")
      searchParts.push(`检索到 ${searchCall.result_count} 条`);
  }
  if (typeof sourceCount === "number" && sourceCount > 0) {
    searchParts.push(`引用来源 ${sourceCount} 条`);
  }
  if (searchParts.length) rows.push(["检索决策", searchParts.join(" · ")]);

  if (!rows.length) return null;

  return (
    <details className="mt-1.5 max-w-[85%] w-full">
      <summary className="inline-flex items-center gap-1.5 cursor-pointer text-[12px] text-muted hover:text-ink list-none select-none">
        <ListTree size={13} strokeWidth={2} />
        本轮系统决策
      </summary>
      <div className="mt-1.5 rounded-xl bg-surface-2 border border-line px-3.5 py-3 flex flex-col gap-2">
        {rows.map(([k, v]) => (
          <div key={k} className="flex flex-col gap-0.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-faint">{k}</span>
            <span className="text-[13px] text-ink-soft leading-relaxed break-words">{v}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

/* ---------------------------------------------------------------- helpers */

function describeToolCall(call: ToolCall): string {
  const args = summarizeArgs(call.arguments);
  const result = truncate(String(call.result ?? "").trim(), 80);
  let text = args ? `${call.name}(${args})` : call.name;
  if (result) text += ` → ${result}`;
  return text;
}

function summarizeArgs(args?: Record<string, unknown>): string {
  if (!args) return "";
  const pairs = Object.entries(args).map(([k, v]) => `${k}=${truncate(formatValue(v), 40)}`);
  return truncate(pairs.join(", "), 80);
}

function formatValue(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
