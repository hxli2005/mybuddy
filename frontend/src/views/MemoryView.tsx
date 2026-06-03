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
  SegmentedControl,
  Tags,
} from "../components/Primitives";
import { queryKeys } from "../state/observability";
import type { MemoryItem } from "../types/api";

type MemoryTab = "archive" | "conversations" | "raw";

export function MemoryView() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<MemoryTab>("archive");
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

  const tabItems = [
    { value: "archive" as const, label: "档案", count: data?.archive.length || 0 },
    { value: "conversations" as const, label: "会话原文", count: data?.conversations.length || 0 },
    { value: "raw" as const, label: "原始记录", count: data?.raw.length || 0 },
  ];

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
        actions={<SegmentedControl items={tabItems} label="记忆分类" onChange={setTab} value={tab} />}
        description="长期记忆是后续回复的素材；这里优先暴露可校正内容。"
        title="记忆"
      />

      {tab === "archive" ? (
        <Panel title="档案记忆" description="编辑或删除会影响后续画像与召回。">
          {data?.archive.length ? (
            <div className="memory-grid">
              {data.archive.map((item) => (
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
            <EmptyState title="暂无档案记忆" text="有价值的长期上下文会在这里聚合。" />
          )}
        </Panel>
      ) : (
        <Panel title={tab === "conversations" ? "会话原文" : "原始记录"} description="只读调试视图，用于核对提取来源。">
          <pre className="json-panel">{JSON.stringify(data?.[tab] || [], null, 2)}</pre>
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
        <strong>{String(item.metadata?.type || "memory")}</strong>
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
