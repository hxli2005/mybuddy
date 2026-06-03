import { Ban, BellRing, Radio } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cancelReminder, fetchReminders } from "../api/client";
import { EmptyState, ErrorState, LoadingState, PageHeader, Panel } from "../components/Primitives";
import { queryKeys } from "../state/observability";
import type { PendingMessage, Reminder } from "../types/api";

export function RemindersView() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.reminders, queryFn: fetchReminders });
  const cancelMutation = useMutation({
    mutationFn: cancelReminder,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.reminders }),
  });

  if (query.isLoading) return <LoadingState label="正在读取提醒" />;
  if (query.error) return <ErrorState error={query.error} />;
  const reminders = query.data?.reminders || [];
  const pending = query.data?.pending_messages || [];
  const pendingCount = reminders.filter((item) => item.status === "pending").length;

  return (
    <section className="view reminders-view">
      <PageHeader
        description={`${pendingCount} 条提醒仍在等待触发，${pending.length} 条消息等待播报。`}
        title="提醒"
      />
      <div className="dashboard-grid">
        <Panel
          actions={
            <span className="panel-pill">
              <BellRing size={15} />
              {pendingCount}
            </span>
          }
          title="提醒队列"
          description="取消只会把状态改为 cancelled，不删除历史。"
        >
          {reminders.length ? (
            <div className="table-list">
              {reminders.map((item) => (
                <ReminderRow
                  item={item}
                  key={item.id}
                  onCancel={() => cancelMutation.mutate(item.id)}
                  pending={cancelMutation.isPending}
                />
              ))}
            </div>
          ) : (
            <EmptyState title="暂无提醒" text="你可以在对话中让 MyBuddy 创建提醒。" />
          )}
        </Panel>

        <Panel
          actions={
            <span className="panel-pill">
              <Radio size={15} />
              {pending.length}
            </span>
          }
          title="待播消息"
          description="这些内容会在合适时机进入会话。"
        >
          {pending.length ? (
            <div className="table-list">
              {pending.map((item, index) => (
                <PendingMessageCard item={item} key={`${item.source}-${index}`} />
              ))}
            </div>
          ) : (
            <EmptyState title="没有待播消息" text="当前没有调度消息等待进入对话。" />
          )}
        </Panel>
      </div>
    </section>
  );
}

function ReminderRow({ item, pending, onCancel }: { item: Reminder; pending: boolean; onCancel: () => void }) {
  const cancellable = item.status === "pending";

  return (
    <article className="row-card reminder-row">
      <div>
        <strong>{item.content}</strong>
        <span>{item.trigger_at}</span>
      </div>
      <span className={`badge ${item.status}`}>{item.status}</span>
      <button
        aria-label="取消提醒"
        className="icon-button"
        data-state={pending ? "loading" : undefined}
        disabled={!cancellable || pending}
        onClick={onCancel}
        title={cancellable ? "取消提醒" : "提醒已结束"}
        type="button"
      >
        <Ban size={15} />
      </button>
    </article>
  );
}

function PendingMessageCard({ item }: { item: PendingMessage }) {
  return (
    <article className="list-card compact-card">
      <header>
        <strong>{item.source}</strong>
        <span>{item.scheduled_at || "待定时间"}</span>
      </header>
      <p>{item.content}</p>
    </article>
  );
}
