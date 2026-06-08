import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Check, Pencil, X } from "lucide-react";
import {
  deleteMemoryItem,
  deleteProfileClaim,
  deleteProfileField,
  fetchMemory,
  fetchProfile,
  updateMemoryItem,
  updateProfileClaim,
  updateProfileField,
} from "../../lib/api";
import { EmptyState, IconButton, Input, SectionLabel, Textarea } from "../../components/ui";
import { queryKeys } from "../../lib/queryKeys";
import { ConfidenceBar, ConfirmDelete, ItemCard, MiniTags, SectionState } from "./common";
import type { MemoryItem, ProfileClaim } from "../../types/api";

export function MemorySection() {
  const qc = useQueryClient();
  const profileQuery = useQuery({ queryKey: queryKeys.profile, queryFn: fetchProfile });
  const memoryQuery = useQuery({ queryKey: queryKeys.memory, queryFn: fetchMemory });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: queryKeys.profile });
    qc.invalidateQueries({ queryKey: queryKeys.memory });
  };

  const fields = Object.entries(profileQuery.data?.fields || {});
  const claims = profileQuery.data?.claims || [];
  const archive = memoryQuery.data?.archive || [];
  const loading = profileQuery.isLoading || memoryQuery.isLoading;
  const error = profileQuery.error || memoryQuery.error;
  const empty = !fields.length && !claims.length && !archive.length;

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

          {claims.length ? (
            <div className="flex flex-col gap-2.5">
              <SectionLabel>候选观察 · {claims.length}</SectionLabel>
              <p className="text-[12px] text-faint px-1 -mt-1">弱线索，反复出现后才会变成长期记忆。</p>
              {claims.map((claim) => (
                <ClaimCard key={claim.sql_id || claim.claim} claim={claim} onChanged={refresh} />
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

function ClaimCard({ claim, onChanged }: { claim: ProfileClaim; onChanged: () => void }) {
  const id = claim.sql_id;
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(claim.claim);
  const [confidence, setConfidence] = useState(claim.confidence || 0.5);
  const update = useMutation({
    mutationFn: () => updateProfileClaim(id!, { claim: text.trim(), confidence }),
    onSuccess: () => {
      setEditing(false);
      onChanged();
    },
  });
  const del = useMutation({ mutationFn: () => deleteProfileClaim(id!), onSuccess: onChanged });

  return (
    <ItemCard>
      <div className="flex items-center justify-between gap-2">
        <ConfidenceBar value={claim.confidence || 0} />
        {id && !editing ? (
          <div className="flex items-center gap-0.5">
            <IconButton icon={Pencil} label="编辑" size={14} onClick={() => { setText(claim.claim); setConfidence(claim.confidence || 0.5); setEditing(true); }} />
            <ConfirmDelete onConfirm={() => del.mutate()} label="删除观察" disabled={del.isPending} />
          </div>
        ) : null}
      </div>
      {id && editing ? (
        <>
          <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          <label className="flex items-center gap-2 text-[12px] text-muted">
            置信度
            <input type="range" min={0} max={1} step={0.05} value={confidence} onChange={(e) => setConfidence(Number(e.target.value))} className="flex-1 accent-[var(--accent)]" />
            <span className="tabular-nums w-9 text-right">{Math.round(confidence * 100)}%</span>
          </label>
          <div className="flex items-center justify-end gap-1">
            <IconButton icon={Check} label="保存" tone="accent" size={15} disabled={update.isPending || !text.trim()} onClick={() => update.mutate()} />
            <IconButton icon={X} label="取消" size={15} onClick={() => setEditing(false)} />
          </div>
        </>
      ) : (
        <>
          <p className="text-[13.5px] text-ink-soft leading-relaxed">{claim.claim}</p>
          <div className="flex flex-wrap gap-1.5 text-[11px] text-faint">
            <span>{claimStatusLabel(claim.status)}</span>
            <span>·</span>
            <span>{claimCategoryLabel(claim.category)}</span>
            {claim.evidence_count ? (
              <>
                <span>·</span>
                <span>{claim.evidence_count} 条证据</span>
              </>
            ) : null}
          </div>
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

function claimStatusLabel(value?: string) {
  const labels: Record<string, string> = { candidate: "候选", active: "追踪中", stable: "稳定" };
  return labels[value || "active"] || "追踪中";
}

function claimCategoryLabel(value?: string) {
  const labels: Record<string, string> = {
    fact: "事实",
    preference: "偏好",
    relationship: "关系",
    emotion_pattern: "模式",
    task: "事项",
    boundary: "边界",
    general: "观察",
  };
  return labels[value || "general"] || "观察";
}
