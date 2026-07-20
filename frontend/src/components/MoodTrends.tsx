import { useQuery } from "@tanstack/react-query";
import { CalendarCheck, Frown, Smile, TrendingUp } from "lucide-react";
import { fetchMoodStats } from "../lib/api";
import { Surface, Spinner, Chip } from "./ui";
import { queryKeys } from "../lib/queryKeys";

const categoryLabels: Record<string, string> = {
  anxiety: "焦虑",
  sadness: "悲伤",
  anger: "愤怒",
  fatigue: "疲惫",
  loneliness: "孤独",
  stress: "压力",
  guilt: "内疚",
  shame: "羞耻",
  fear: "恐惧",
  disappointment: "失望",
  boredom: "无聊",
  calm: "平静",
  joy: "喜悦",
  gratitude: "感激",
  excitement: "兴奋",
};

export function MoodTrends() {
  const statsQuery = useQuery({
    queryKey: queryKeys.moodStats,
    queryFn: fetchMoodStats,
    staleTime: 30000,
  });

  const stats = statsQuery.data;

  if (statsQuery.isLoading) {
    return (
      <Surface className="p-4 flex items-center justify-center h-32">
        <Spinner size={18} />
      </Surface>
    );
  }

  if (!stats || stats.total_records === 0) return null;

  const items = [
    {
      icon: TrendingUp,
      label: "平均分",
      value: stats.avg_score != null ? `${stats.avg_score}/10` : "—",
    },
    {
      icon: CalendarCheck,
      label: "连续签到",
      value: stats.streak > 0 ? `${stats.streak} 天` : "—",
    },
    {
      icon: Smile,
      label: "状态最好",
      value: stats.best_day ? stats.best_day.slice(5) : "—",
    },
    {
      icon: Frown,
      label: "状态最低",
      value: stats.worst_day ? stats.worst_day.slice(5) : "—",
    },
  ];

  const topCategories = Object.entries(stats.categories || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  return (
    <Surface className="p-4">
      <p className="text-[12px] font-semibold uppercase tracking-wide text-faint mb-3">
        情绪统计
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-2 gap-2">
        {items.map(({ icon: Icon, label, value }) => (
          <div key={label} className="rounded-xl bg-surface-2 px-3.5 py-3 flex items-center gap-3">
            <Icon size={17} strokeWidth={1.8} className="text-accent shrink-0" />
            <div className="min-w-0">
              <p className="text-[11px] text-muted">{label}</p>
              <p className="text-[14px] font-semibold text-ink truncate">{value}</p>
            </div>
          </div>
        ))}
      </div>
      {topCategories.length > 0 ? (
        <div className="mt-3">
          <p className="text-[11px] text-muted mb-1.5">常见情绪</p>
          <div className="flex flex-wrap gap-1.5">
            {topCategories.map(([cat, count]) => (
              <Chip key={cat} tone="accent" className="h-6 text-[11px]">
                {categoryLabels[cat] || cat} · {count}
              </Chip>
            ))}
          </div>
        </div>
      ) : null}
    </Surface>
  );
}
