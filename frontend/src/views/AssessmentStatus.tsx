import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, History, LogIn, Shield } from "lucide-react";
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Surface, EmptyState, Spinner, Button } from "../components/ui";
import { fetchAssessmentHistory, fetchAssessmentStatus } from "../lib/api";
import { queryKeys } from "../lib/queryKeys";
import { useAuth } from "../lib/auth";
import { useRouter } from "../lib/router";
import type { AssessmentDimensionStatus, AssessmentStatusResponse } from "../types/api";

const phq9Names = [
  "兴趣与愉悦感", "情绪低落", "睡眠问题", "精力不足",
  "食欲问题", "自我评价", "注意力问题", "精神运动", "自伤意念",
];

const gad7Names = [
  "紧张不安", "无法停止担忧", "过度担忧", "难以放松",
  "坐立不安", "易怒", "害怕失控",
];

const statusStyle: Record<string, { bg: string; label: string }> = {
  unasked: { bg: "bg-surface-2", label: "—" },
  asked: { bg: "bg-accent-soft", label: "…" },
  answered: { bg: "bg-accent/20", label: "✓" },
  scored: { bg: "bg-positive/15", label: "✓" },
};

export function AssessmentStatus() {
  const { isLoggedIn, loading: authLoading } = useAuth();
  const { navigate } = useRouter();

  const statusQuery = useQuery({
    queryKey: queryKeys.assessment,
    queryFn: () => fetchAssessmentStatus().catch(() => null),
    enabled: isLoggedIn,
    staleTime: 60000,
  });

  const historyQuery = useQuery({
    queryKey: queryKeys.assessmentHistory,
    queryFn: () => fetchAssessmentHistory().catch(() => null),
    enabled: isLoggedIn,
    staleTime: 60000,
  });

  if (authLoading) {
    return (
      <div className="h-full flex items-center justify-center">
        <Spinner size={20} />
      </div>
    );
  }

  if (!isLoggedIn) {
    return (
      <div className="h-full flex items-center justify-center">
        <EmptyState
          icon={LogIn}
          title="登录后查看我的状态"
          text="了解小布在对话中逐渐了解你的过程"
          action={
            <Button size="sm" onClick={() => navigate("login")}>
              登录 / 注册
            </Button>
          }
        />
      </div>
    );
  }

  const data: AssessmentStatusResponse | null | undefined = statusQuery.data;
  const phq9Covered = data?.phq9.filter((d) => d.status === "scored" || d.status === "answered").length || 0;
  const gad7Covered = data?.gad7.filter((d) => d.status === "scored" || d.status === "answered").length || 0;
  const totalCovered = phq9Covered + gad7Covered;
  const totalDims = 16; // 9 + 7

  const cycles = historyQuery.data?.cycles || [];
  const historyData = buildHistoryChartData(cycles);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl px-4 sm:px-5 py-6 flex flex-col gap-5">
        {/* 总览 */}
        <Surface className="p-4 text-center">
          <Activity size={22} strokeWidth={1.8} className="text-accent mx-auto mb-2" />
          <p className="font-semibold text-ink text-[15px]">
            {statusQuery.isLoading ? "加载中…" : `关于你的了解 ${totalCovered}/${totalDims}`}
          </p>
          <p className="text-[12px] text-muted mt-0.5">
            这些是我们在对话中自然了解到的，你可以随时查看
          </p>
          {/* 进度环 */}
          <div className="mt-3 flex justify-center">
            <div className="relative h-20 w-20">
              <svg viewBox="0 0 64 64" className="h-full w-full -rotate-90">
                <circle cx="32" cy="32" r="28" fill="none" stroke="var(--line)" strokeWidth="6" />
                <circle
                  cx="32" cy="32" r="28"
                  fill="none"
                  stroke="var(--accent)"
                  strokeWidth="6"
                  strokeLinecap="round"
                  strokeDasharray={`${(totalCovered / totalDims) * 176} 176`}
                />
              </svg>
              <span className="absolute inset-0 flex items-center justify-center text-[13px] font-semibold text-ink">
                {totalCovered}/{totalDims}
              </span>
            </div>
          </div>
        </Surface>

        {/* 免责声明 */}
        <div className="flex items-start gap-2.5 rounded-xl bg-negative/5 border border-negative/15 px-3.5 py-3">
          <Shield size={15} strokeWidth={1.8} className="text-negative shrink-0 mt-0.5" />
          <p className="text-[11.5px] text-ink-soft leading-relaxed">
            这不是诊断工具，分数高不代表有疾病，分数低也不代表没事。小布只是在用这些量表作为了解你的参考，让你更好地观察自己的状态变化。如有需要，建议和心理咨询师或医生讨论。
          </p>
        </div>

        <DimensionSection
          title="PHQ-9 情绪状态"
          dims={data?.phq9 || []}
          names={phq9Names}
          loading={statusQuery.isLoading}
          total={data?.phq9_total}
          totalMax={27}
          level={data?.phq9_level}
        />

        <DimensionSection
          title="GAD-7 焦虑状态"
          dims={data?.gad7 || []}
          names={gad7Names}
          loading={statusQuery.isLoading}
          total={data?.gad7_total}
          totalMax={21}
          level={data?.gad7_level}
        />

        {/* 历史趋势 */}
        <div>
          <p className="text-[12px] font-semibold uppercase tracking-wide text-faint px-1 mb-2 flex items-center gap-1.5">
            <History size={13} strokeWidth={2} />
            状态变化
          </p>
          <Surface inset className="p-4">
            {historyQuery.isLoading ? (
              <div className="flex justify-center py-4"><Spinner size={18} /></div>
            ) : historyData.length === 0 ? (
              <p className="text-[12.5px] text-muted text-center py-3">
                完成一轮了解后，这里会出现你的状态变化曲线
              </p>
            ) : (
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={historyData} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="var(--muted)" />
                    <YAxis tick={{ fontSize: 11 }} stroke="var(--muted)" domain={[0, 27]} />
                    <Tooltip
                      contentStyle={{ fontSize: 12, borderRadius: 12 }}
                      formatter={(value, name) => [
                        value as number,
                        name === "phq9" ? "情绪 (PHQ-9)" : "焦虑 (GAD-7)",
                      ]}
                    />
                    <Line type="monotone" dataKey="phq9" stroke="var(--accent)" strokeWidth={2} dot connectNulls />
                    <Line type="monotone" dataKey="gad7" stroke="var(--positive, #5f8b6a)" strokeWidth={2} dot connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </Surface>
        </div>
      </div>
    </div>
  );
}

function DimensionSection({
  title,
  dims,
  names,
  loading,
  total,
  totalMax,
  level,
}: {
  title: string;
  dims: AssessmentDimensionStatus[];
  names: string[];
  loading: boolean;
  total?: number;
  totalMax: number;
  level?: string;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const selected = expanded != null ? dims[expanded] : null;

  return (
    <div>
      <p className="text-[12px] font-semibold uppercase tracking-wide text-faint px-1 mb-2">
        {title}
      </p>
      <Surface inset className="p-4">
        {loading ? (
          <div className="flex justify-center py-4"><Spinner size={18} /></div>
        ) : (
          <div className="grid grid-cols-3 gap-1.5">
            {dims.map((d, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setExpanded(expanded === i ? null : i)}
                className={`rounded-lg ${statusStyle[d.status]?.bg || "bg-surface-2"} px-2.5 py-2 text-center transition-shadow ${
                  expanded === i ? "ring-1 ring-accent" : ""
                } ${d.status === "scored" ? "cursor-pointer" : "cursor-default"}`}
              >
                <p className="text-[10.5px] text-muted">{names[i] || `维度${i + 1}`}</p>
                <p className="text-[13px] font-semibold text-ink mt-0.5">
                  {d.status === "scored" ? d.score : statusStyle[d.status]?.label || "—"}
                </p>
              </button>
            ))}
          </div>
        )}

        {selected && selected.status === "scored" ? (
          <div className="mt-3 rounded-xl bg-surface-2 border border-line px-3.5 py-3 animate-fade-in">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-faint mb-1">
              {names[expanded!] || ""} · 评分 {selected.score}/3
            </p>
            {selected.source_conversation ? (
              <p className="text-[12.5px] text-ink-soft leading-relaxed">
                依据你说过的：「{selected.source_conversation}」
              </p>
            ) : (
              <p className="text-[12.5px] text-muted">这一项是从日常对话中了解到的。</p>
            )}
            {selected.scored_at ? (
              <p className="text-[11px] text-faint mt-1">
                记录于 {selected.scored_at.slice(0, 10)}
              </p>
            ) : null}
          </div>
        ) : null}

        {total != null ? (
          <p className="text-[13px] font-medium mt-3 text-center text-ink">
            总分 {total}/{totalMax}{level ? ` · ${level}` : ""}
          </p>
        ) : null}
      </Surface>
    </div>
  );
}

function buildHistoryChartData(
  cycles: Array<{ assessment_type: string; total_score: number; completed_at?: string | null }>,
) {
  // 按完成日期聚合:同一天的 phq9/gad7 合并为一个点
  const byDate = new Map<string, { date: string; phq9?: number; gad7?: number }>();
  for (const c of [...cycles].reverse()) {
    const date = (c.completed_at || "").slice(5, 10) || "—";
    const entry = byDate.get(date) || { date };
    if (c.assessment_type === "phq9") entry.phq9 = c.total_score;
    if (c.assessment_type === "gad7") entry.gad7 = c.total_score;
    byDate.set(date, entry);
  }
  return Array.from(byDate.values());
}
