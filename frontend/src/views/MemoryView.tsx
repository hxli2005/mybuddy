import { Check, Pencil, Trash2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { deleteMemoryItem, fetchMemory, updateMemoryItem } from "../api/client";
import {
  CancelEditButton,
  ConfirmAction,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  Panel,
  Tags,
} from "../components/Primitives";
import { queryKeys } from "../state/observability";
import type { MemoryItem } from "../types/api";

type MemoryKind = "profile" | "preference" | "shared_moment" | "open_thread";

export function MemoryView() {
  const queryClient = useQueryClient();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftContent, setDraftContent] = useState("");
  const query = useQuery({ queryKey: queryKeys.memory, queryFn: fetchMemory });
  const updateMutation = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) => updateMemoryItem(id, { content }),
    onSuccess: () => {
      setEditingId(null);
      queryClient.invalidateQueries({ queryKey: queryKeys.memory });
      queryClient.invalidateQueries({ queryKey: queryKeys.profile });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: deleteMemoryItem,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.memory });
      queryClient.invalidateQueries({ queryKey: queryKeys.profile });
    },
  });

  if (query.isLoading) return <LoadingState label="正在读取记忆" />;
  if (query.error) return <ErrorState error={query.error} />;
  const data = query.data;
  const groups = memoryGroups(data?.archive || []);

  function startEdit(id: string, content: string) {
    setEditingId(id);
    setDraftContent(content);
  }

  function saveEdit(id: string) {
    const clean = draftContent.trim();
    if (!clean) return;
    updateMutation.mutate({ id, content: clean });
  }

  return (
    <section className="view">
      <PageHeader
        description="这里只放会影响后续回复的少量长期记忆。改掉或删除后，小布就不会继续按旧内容回应。"
        title="记忆"
      />

      {groups.some((group) => group.items.length) ? (
        groups.map((group) => (
          <Panel description={group.description} key={group.id} title={group.title}>
            {group.items.length ? (
              <div className="memory-grid">
                {group.items.map((item) => (
                  <MemoryCard
                    deletePending={deleteMutation.isPending}
                    editing={editingId === item.id}
                    item={item}
                    key={item.id}
                    onCancel={() => setEditingId(null)}
                    onDelete={() => deleteMutation.mutate(item.id)}
                    onDraft={setDraftContent}
                    onEdit={() => startEdit(item.id, item.content)}
                    onSave={() => saveEdit(item.id)}
                    savePending={updateMutation.isPending}
                    value={draftContent}
                  />
                ))}
              </div>
            ) : (
              <EmptyState title="暂时为空" text="相关内容出现后会自动沉淀到这里。" />
            )}
          </Panel>
        ))
      ) : (
        <Panel title="还没有长期记忆" description="普通聊天不会全部进入记忆，只有明确、可复用的内容才会留下。">
          <EmptyState title="暂无记忆" text="继续聊天后，小布会逐渐记住少量重要信息。" />
        </Panel>
      )}
    </section>
  );
}

function MemoryCard({
  item,
  editing,
  value,
  savePending,
  deletePending,
  onEdit,
  onCancel,
  onDraft,
  onSave,
  onDelete,
}: {
  item: MemoryItem;
  editing: boolean;
  value: string;
  savePending: boolean;
  deletePending: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onDraft: (value: string) => void;
  onSave: () => void;
  onDelete: () => void;
}) {
  const tags = Array.isArray(item.metadata?.tags) ? item.metadata.tags.map(String) : [];
  const meta = memoryMeta(item);

  return (
    <article className="list-card memory-card">
      <header>
        <strong>{memoryKindLabel(memoryKind(item))}</strong>
        {typeof item.score === "number" ? <span>{item.score.toFixed(2)}</span> : null}
      </header>
      {editing ? (
        <>
          <textarea aria-label="记忆内容" onChange={(event) => onDraft(event.target.value)} rows={5} value={value} />
          <div className="inline-actions">
            <button
              aria-label="保存记忆"
              className="icon-button"
              data-state={savePending ? "loading" : undefined}
              disabled={savePending || !value.trim()}
              onClick={onSave}
              title="保存"
              type="button"
            >
              <Check size={15} />
            </button>
            <CancelEditButton onClick={onCancel} label="取消编辑记忆" />
          </div>
        </>
      ) : (
        <>
          <p>{item.content}</p>
          {meta.length ? <div className="memory-meta">{meta.map((part) => <span key={part}>{part}</span>)}</div> : null}
          <Tags values={tags} />
          <div className="inline-actions">
            <button aria-label="编辑记忆" className="icon-button" onClick={onEdit} title="编辑" type="button">
              <Pencil size={15} />
            </button>
            <ConfirmAction
              confirmLabel="确认删除记忆"
              disabled={deletePending}
              label="删除记忆"
              onConfirm={onDelete}
              title="删除"
            >
              <Trash2 size={15} />
            </ConfirmAction>
          </div>
        </>
      )}
    </article>
  );
}

function memoryGroups(items: MemoryItem[]) {
  const groups: Array<{
    id: MemoryKind;
    title: string;
    description: string;
    items: MemoryItem[];
  }> = [
    {
      id: "profile",
      title: "关于你",
      description: "稳定事实和明确背景，不放临时情绪。",
      items: [] as MemoryItem[],
    },
    {
      id: "preference",
      title: "你的偏好",
      description: "喜欢、不喜欢、避雷和更适合你的回应方式。",
      items: [] as MemoryItem[],
    },
    {
      id: "shared_moment",
      title: "我们经历过的事",
      description: "之后可以轻轻想起的共同片段。",
      items: [] as MemoryItem[],
    },
    {
      id: "open_thread",
      title: "小布正在惦记的事",
      description: "有具体由头、还没收尾的话题。",
      items: [] as MemoryItem[],
    },
  ];
  const byId = Object.fromEntries(groups.map((group) => [group.id, group])) as Record<MemoryKind, (typeof groups)[number]>;
  for (const item of items) {
    byId[memoryKind(item)].items.push(item);
  }
  return groups;
}

function memoryKind(item: MemoryItem): MemoryKind {
  const type = String(item.metadata?.type || "profile");
  if (type === "open_thread" || type === "shared_moment" || type === "preference" || type === "profile") {
    return type;
  }
  if (type === "anti_preference" || type === "relationship_note" || type === "character_note") {
    return "preference";
  }
  return "profile";
}

function memoryKindLabel(kind: MemoryKind): string {
  const labels = {
    profile: "关于你",
    preference: "偏好",
    shared_moment: "共同经历",
    open_thread: "惦记的事",
  };
  return labels[kind];
}

function memoryMeta(item: MemoryItem): string[] {
  const meta = item.metadata || {};
  const parts: string[] = [];
  const status = typeof meta.status === "string" ? meta.status : "";
  if (status && status !== "active") parts.push(statusLabel(status));
  const source = typeof meta.source === "string" ? meta.source : "";
  if (source) parts.push(sourceLabel(source));
  const eventTime = typeof meta.event_time === "string" ? meta.event_time : "";
  if (eventTime) parts.push(`事件 ${eventTime}`);
  const created = typeof meta.created_at === "string" ? meta.created_at : "";
  if (created) parts.push(`记录 ${shortTime(created)}`);
  const updated = typeof meta.updated_at === "string" ? meta.updated_at : "";
  if (updated && updated !== created) parts.push(`更新 ${shortTime(updated)}`);
  const lastSeen = typeof meta.last_seen_at === "string" ? meta.last_seen_at : "";
  if (lastSeen && lastSeen !== updated && lastSeen !== created) parts.push(`最近出现 ${shortTime(lastSeen)}`);
  const count = Number(meta.occurrence_count || 0);
  if (count > 1) parts.push(`${count} 次`);
  return parts;
}

function shortTime(value: string): string {
  return value.replace("T", " ").slice(0, 16);
}

function sourceLabel(value: string): string {
  const labels: Record<string, string> = {
    fact_extraction: "自动事实",
    relationship_extraction: "关系抽取",
    profile_claim: "画像命题",
    user_note: "用户笔记",
    manual: "手动",
  };
  return labels[value] || value;
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    stale: "已过期",
    resolved: "已收尾",
    archived: "已归档",
  };
  return labels[value] || value;
}
