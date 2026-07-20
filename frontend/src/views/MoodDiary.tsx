import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Heart, LogIn } from "lucide-react";
import { MoodChart } from "../components/MoodChart";
import { MoodTrends } from "../components/MoodTrends";
import { CheckInDialog } from "../components/CheckInDialog";
import { Button, EmptyState, Surface, Chip, Spinner } from "../components/ui";
import { fetchMoodRecords, moodCheckin } from "../lib/api";
import { queryKeys } from "../lib/queryKeys";
import { useAuth } from "../lib/auth";
import { useRouter } from "../lib/router";

type MoodRecord = {
  date: string;
  score: number | null;
  notes?: string;
  category?: string;
};

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

const emojiForScore = (s: number | null): string => {
  if (s == null) return "—";
  if (s >= 8) return "☺️";
  if (s >= 6) return "🙂";
  if (s >= 4) return "😐";
  if (s >= 2) return "😔";
  return "😢";
};

export function MoodDiary() {
  const { isLoggedIn, loading: authLoading } = useAuth();
  const { navigate } = useRouter();
  const queryClient = useQueryClient();
  const [checkinOpen, setCheckinOpen] = useState(false);

  const moodQuery = useQuery({
    queryKey: queryKeys.mood,
    queryFn: () => fetchMoodRecords(30).catch(() => null),
    enabled: isLoggedIn,
    staleTime: 30000,
  });

  const [checkinError, setCheckinError] = useState("");

  const checkinMutation = useMutation({
    mutationFn: ({ score, notes }: { score: number; notes: string }) =>
      moodCheckin(score, notes),
    onSuccess: () => {
      setCheckinError("");
      setCheckinOpen(false);
      queryClient.invalidateQueries({ queryKey: queryKeys.mood });
      queryClient.invalidateQueries({ queryKey: queryKeys.moodStats });
    },
    onError: (err) => {
      setCheckinError(err instanceof Error ? err.message : "签到失败");
    },
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
          title="登录后开启情绪日记"
          text="记录每天的心情变化，发现自己的情绪模式"
          action={
            <Button size="sm" onClick={() => navigate("login")}>
              登录 / 注册
            </Button>
          }
        />
      </div>
    );
  }

  const records: MoodRecord[] = moodQuery.data?.records || [];
  const chartData = records.map((r) => ({
    date: r.date?.slice(5, 11) || r.date, // MM-DD
    score: r.score,
    notes: r.notes,
  })).reverse();

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl px-4 sm:px-5 py-6 flex flex-col gap-5">
        {/* 签到 + 统计(移动端纵向,PC 端与图表并排) */}
        <div className="flex flex-col lg:flex-row gap-5 lg:items-start">
          <div className="flex flex-col gap-5 lg:w-[300px] lg:shrink-0">
            <Surface className="p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Heart size={18} strokeWidth={1.8} className="text-accent" />
                  <span className="font-semibold text-ink text-[15px]">今天</span>
                </div>
                <Button size="sm" variant="secondary" onClick={() => { setCheckinError(""); setCheckinOpen(true); }}>
                  签到
                </Button>
              </div>
              {checkinError ? (
                <p className="text-[12px] text-negative mt-2">{checkinError}</p>
              ) : null}
              {records.length > 0 && records[records.length - 1] ? (
                <div className="flex items-center gap-3">
                  <span className="text-2xl">{emojiForScore(records[records.length - 1].score)}</span>
                  <div>
                    <p className="text-[13px] text-ink">
                      最近记录 · {records[records.length - 1].score}/10
                    </p>
                    {records[records.length - 1].notes ? (
                      <p className="text-[12px] text-muted mt-0.5 line-clamp-1">
                        {records[records.length - 1].notes}
                      </p>
                    ) : null}
                  </div>
                </div>
              ) : (
                <p className="text-[13px] text-muted">今天还没有记录心情，来签个到吧</p>
              )}
            </Surface>

            <MoodTrends />
          </div>

          {/* 趋势图 */}
          <Surface className="p-4 flex-1 min-w-0 w-full">
            <p className="text-[12px] font-semibold uppercase tracking-wide text-faint mb-3">
              情绪趋势 · 近 30 天
            </p>
            {moodQuery.isLoading ? (
              <div className="flex items-center justify-center h-48">
                <Spinner size={18} />
              </div>
            ) : (
              <MoodChart data={chartData} />
            )}
          </Surface>
        </div>

        {/* 历史记录 */}
        <div>
          <p className="text-[12px] font-semibold uppercase tracking-wide text-faint px-1 mb-3">
            最近记录
          </p>
          {moodQuery.isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Spinner size={18} />
            </div>
          ) : records.length === 0 ? (
            <EmptyState
              icon={Heart}
              title="还没有情绪记录"
              text="聊天或签到后这里会出现你的情绪变化"
            />
          ) : (
            <div className="flex flex-col gap-2">
              {records.slice().reverse().slice(0, 20).map((r, i) => (
                <Surface key={`${r.date}-${i}`} inset className="px-4 py-3 flex items-center gap-3">
                  <span className="text-xl shrink-0">{emojiForScore(r.score)}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-medium text-ink">
                        {r.score}/10
                      </span>
                      <span className="text-[11.5px] text-faint">{r.date}</span>
                      {r.category ? (
                        <Chip tone="accent" className="h-5 text-[10.5px] px-2">
                          {categoryLabels[r.category] || r.category}
                        </Chip>
                      ) : null}
                    </div>
                    {r.notes ? (
                      <p className="text-[12px] text-ink-soft mt-0.5 line-clamp-1">
                        {r.notes}
                      </p>
                    ) : null}
                  </div>
                </Surface>
              ))}
            </div>
          )}
        </div>
      </div>

      <CheckInDialog
        open={checkinOpen}
        onClose={() => setCheckinOpen(false)}
        onSubmit={(score, notes) => checkinMutation.mutate({ score, notes })}
        loading={checkinMutation.isPending}
      />
    </div>
  );
}
