import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Check, Pencil, X } from "lucide-react";
import {
  deleteMemoryItem,
  deleteProfileField,
  fetchMemory,
  fetchProfile,
  updateMemoryItem,
  updateProfileField,
} from "../../lib/api";
import { EmptyState, IconButton, Input, SectionLabel, Textarea } from "../../components/ui";
import { queryKeys } from "../../lib/queryKeys";
import { ConfirmDelete, ItemCard, MiniTags, SectionState } from "./common";
import type { MemoryItem } from "../../types/api";

export function MemorySection() {
  const qc = useQueryClient();
  const profileQuery = useQuery({ queryKey: queryKeys.profile, queryFn: fetchProfile });
  const memoryQuery = useQuery({ queryKey: queryKeys.memory, queryFn: fetchMemory });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: queryKeys.profile });
    qc.invalidateQueries({ queryKey: queryKeys.memory });
  };

  const fields = Object.entries(profileQuery.data?.fields || {});
  const archive = memoryQuery.data?.archive || [];
  const loading = profileQuery.isLoading || memoryQuery.isLoading;
  const error = profileQuery.error || memoryQuery.error;
  const empty = !fields.length && !archive.length;

  return (
    <SectionState loading={loading} error={error}>
      {empty ? (
        <EmptyState icon={Brain} title="还没记住什么" text="多聊几次，小布会慢慢记住关于你的重要信息。" />
      ) : (
        <div className="flex flex-col gap-5">
          <div className="flex flex-col gap-2.5">
            <SectionLabel>明确字段 · {fields.length}</SectionLabel>
            {fields.length ? (
              fields.map(([key, value]) => <FieldCard key={key} fieldKey={key} value={value} onChanged={refresh} />)
            ) : (
              <p className="text-[13px] text-faint px-1">名字、长期偏好这类会出现在这里。</p>
            )}
          </div>

          {archive.length ? (
            <div className="flex flex-col gap-2.5">
              <SectionLabel>长期记忆 · {archive.length}</SectionLabel>
              {archive.map((item) => (
                <MemoryCard key={item.id} item={item} onChanged={refresh} />
              ))}
            </div>
          ) : null}
        </div>
      )}
    </SectionState>
  );
}

function FieldCard({ fieldKey, value, onChanged }: { fieldKey: string; value: string; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const update = useMutation({
    mutationFn: () => updateProfileField(fieldKey, draft.trim()),
    onSuccess: () => {
      setEditing(false);
      onChanged();
    },
  });
  const del = useMutation({ mutationFn: () => deleteProfileField(fieldKey), onSuccess: onChanged });

  return (
    <ItemCard>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-accent">{fieldKey}</span>
        {!editing ? (
          <div className="flex items-center gap-0.5">
            <IconButton icon={Pencil} label="编辑" size={14} onClick={() => { setDraft(value); setEditing(true); }} />
            <ConfirmDelete onConfirm={() => del.mutate()} label="删除字段" disabled={del.isPending} />
          </div>
        ) : null}
      </div>
      {editing ? (
        <div className="flex items-center gap-1">
          <Input value={draft} onChange={(e) => setDraft(e.target.value)} className="h-9" />
          <IconButton icon={Check} label="保存" tone="accent" size={15} disabled={update.isPending || !draft.trim()} onClick={() => update.mutate()} />
          <IconButton icon={X} label="取消" size={15} onClick={() => setEditing(false)} />
        </div>
      ) : (
        <p className="text-[14px] text-ink leading-relaxed">{value}</p>
      )}
    </ItemCard>
  );
}

function MemoryCard({ item, onChanged }: { item: MemoryItem; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.content);
  const update = useMutation({
    mutationFn: () => updateMemoryItem(item.id, { content: draft.trim() }),
    onSuccess: () => {
      setEditing(false);
      onChanged();
    },
  });
  const del = useMutation({ mutationFn: () => deleteMemoryItem(item.id), onSuccess: onChanged });
  const tags = Array.isArray(item.metadata?.tags) ? item.metadata.tags.map(String) : [];

  return (
    <ItemCard>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11.5px] font-medium text-muted">{memoryKindLabel(item)}</span>
        {!editing ? (
          <div className="flex items-center gap-0.5">
            <IconButton icon={Pencil} label="编辑" size={14} onClick={() => { setDraft(item.content); setEditing(true); }} />
            <ConfirmDelete onConfirm={() => del.mutate()} label="删除记忆" disabled={del.isPending} />
          </div>
        ) : null}
      </div>
      {editing ? (
        <>
          <Textarea rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="flex items-center justify-end gap-1">
            <IconButton icon={Check} label="保存" tone="accent" size={15} disabled={update.isPending || !draft.trim()} onClick={() => update.mutate()} />
            <IconButton icon={X} label="取消" size={15} onClick={() => setEditing(false)} />
          </div>
        </>
      ) : (
        <>
          <p className="text-[13.5px] text-ink-soft leading-relaxed whitespace-pre-wrap">{item.content}</p>
          <MiniTags values={tags} />
        </>
      )}
    </ItemCard>
  );
}

function memoryKindLabel(item: MemoryItem): string {
  const type = String(item.metadata?.type || "profile");
  const labels: Record<string, string> = {
    profile: "关于你",
    preference: "偏好",
    anti_preference: "避雷",
    shared_moment: "共同经历",
    open_thread: "惦记的事",
    relationship_note: "关系",
    character_note: "设定",
  };
  return labels[type] || "记忆";
}
