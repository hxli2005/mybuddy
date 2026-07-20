import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip } from "recharts";
import { cn } from "../lib/cn";

type MoodDataPoint = {
  date: string;
  score: number | null;
  notes?: string;
};

type Props = {
  data: MoodDataPoint[];
  days?: 7 | 14 | 30;
  className?: string;
};

const emojiForScore = (s: number | null): string => {
  if (s == null) return "—";
  if (s >= 8) return "☺️";
  if (s >= 6) return "🙂";
  if (s >= 4) return "😐";
  if (s >= 2) return "😔";
  return "😢";
};

export function MoodChart({ data, className }: Props) {
  const hasData = data.some((d) => d.score != null);

  if (!hasData) {
    return (
      <div className={cn("flex items-center justify-center h-48 text-[13px] text-muted", className)}>
        还没有情绪数据，开始聊天或签到后这里会出现趋势图
      </div>
    );
  }

  return (
    <div className={cn("w-full", className)}>
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={data} margin={{ top: 8, right: 4, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="moodGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.18} />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="date"
            axisLine={false}
            tickLine={false}
            tick={{ fontSize: 11, fill: "var(--muted)" }}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={[0, 10]}
            ticks={[0, 2, 4, 6, 8, 10]}
            axisLine={false}
            tickLine={false}
            tick={{ fontSize: 11, fill: "var(--muted)" }}
            width={24}
          />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload as MoodDataPoint;
              return (
                <div className="rounded-xl bg-surface border border-line shadow-card px-3 py-2 text-[12px]">
                  <p className="font-medium text-ink">
                    {d.date} · {emojiForScore(d.score)} {d.score ?? "—"}/10
                  </p>
                  {d.notes ? <p className="text-muted mt-0.5 max-w-[180px]">{d.notes}</p> : null}
                </div>
              );
            }}
          />
          <Area
            type="monotone"
            dataKey="score"
            stroke="var(--accent)"
            strokeWidth={2}
            fill="url(#moodGradient)"
            dot={{ r: 3, fill: "var(--accent)", strokeWidth: 0 }}
            activeDot={{ r: 5, fill: "var(--accent)", stroke: "var(--bg)", strokeWidth: 2 }}
            connectNulls
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
