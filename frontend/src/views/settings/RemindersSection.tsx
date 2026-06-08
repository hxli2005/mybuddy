import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Bell, Radio } from "lucide-react";
import { cancelReminder, fetchReminders } from "../../lib/api";
import { Chip, EmptyState, IconButton, SectionLabel } from "../../components/ui";
import { queryKeys } from "../../lib/queryKeys";
import { ItemCard, SectionState } from "./common";
import type { PendingMessage, Reminder } from "../../types/api";

const statusTone: Record<string, "neutral" | "accent" | "positive" | "negative"> = {
  pending: "accent",
  fired: "positive",
  cancelled: "neutral",
};

export function RemindersSection() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.reminders, queryFn: fetchReminders });
  const cancelMutation = useMutation({
    mutationFn: cancelReminder,
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.reminders }),
  });

  const reminders = query.data?.reminders || [];
  const pending = query.data?.pending_messages || [];

  return (
    <SectionState loading={query.isLoading} error={query.error}>
      <div className="flex flex-col gap-5">
        <div className="flex flex-col gap-2.5">
          <SectionLabel>提醒队列 · {reminders.filter((r) => r.status === "pending").length}</SectionLabel>
          {reminders.length ? (
            reminders.map((item) => (
              <ReminderCard
                key={item.id}
                item={item}
                onCancel={() => cancelMutation.mutate(item.id)}
                disabled={cancelMutation.isPending}
              />
            ))
          ) : (
            <EmptyState icon={Bell} title="还没有提醒" text="在对话里跟小布说“提醒我…”就行。" />
          )}
        </div>

        {pending.length ? (
          <div className="flex flex-col gap-2.5">
            <SectionLabel>待播消息 · {pending.length}</SectionLabel>
            {pending.map((item, i) => (
              <PendingCard key={`${item.source}-${i}`} item={item} />
            ))}
          </div>
        ) : null}
      </div>
    </SectionState>
  );
}

function ReminderCard({ item, onCancel, disabled }: { item: Reminder; onCancel: () => void; disabled: boolean }) {
  const cancellable = item.status === "pending";
  return (
    <ItemCard>
      <div className="flex items-start justify-between gap-2">
        <strong className="text-[14px] font-medium text-ink leading-snug">{item.content}</strong>
        <Chip tone={statusTone[item.status] || "neutral"}>{item.status}</Chip>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] text-muted tabular-nums">{formatTrigger(item.trigger_at)}</span>
        {cancellable ? <IconButton icon={Ban} label="取消提醒" size={15} onClick={onCancel} disabled={disabled} /> : null}
      </div>
    </ItemCard>
  );
}

function PendingCard({ item }: { item: PendingMessage }) {
  return (
    <ItemCard>
      <div className="flex items-center gap-1.5 text-[11.5px] text-accent">
        <Radio size={12} />
        {item.source}
      </div>
      <p className="text-[13.5px] text-ink-soft leading-relaxed">{item.content}</p>
      <span className="text-[11px] text-faint">{item.scheduled_at || "待定时间"}</span>
    </ItemCard>
  );
}

function formatTrigger(value: string) {
  if (!value) return "";
  return value.slice(0, 16).replace("T", " ");
}
