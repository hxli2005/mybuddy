import { Check, Pencil, Trash2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  deleteProfileClaim,
  deleteProfileField,
  fetchProfile,
  updateProfileClaim,
  updateProfileField,
} from "../api/client";
import {
  CancelEditButton,
  ConfidenceMeter,
  ConfirmAction,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  Panel,
} from "../components/Primitives";
import { queryKeys } from "../state/observability";
import type { ProfileClaim } from "../types/api";

export function ProfileView() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.profile, queryFn: fetchProfile });
  const [editingField, setEditingField] = useState<string | null>(null);
  const [fieldValue, setFieldValue] = useState("");
  const [editingClaim, setEditingClaim] = useState<number | null>(null);
  const [claimValue, setClaimValue] = useState("");
  const [claimConfidence, setClaimConfidence] = useState(0.5);

  const refreshProfile = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.profile });
  };
  const refreshProfileAndMemory = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.profile });
    queryClient.invalidateQueries({ queryKey: queryKeys.memory });
  };
  const updateFieldMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) => updateProfileField(key, value),
    onSuccess: () => {
      setEditingField(null);
      refreshProfile();
    },
  });
  const deleteFieldMutation = useMutation({
    mutationFn: deleteProfileField,
    onSuccess: refreshProfile,
  });
  const updateClaimMutation = useMutation({
    mutationFn: ({ id, claim, confidence }: { id: number; claim: string; confidence: number }) =>
      updateProfileClaim(id, { claim, confidence }),
    onSuccess: () => {
      setEditingClaim(null);
      refreshProfileAndMemory();
    },
  });
  const deleteClaimMutation = useMutation({
    mutationFn: deleteProfileClaim,
    onSuccess: refreshProfileAndMemory,
  });

  if (query.isLoading) return <LoadingState label="正在读取画像" />;
  if (query.error) return <ErrorState error={query.error} />;
  const fields = Object.entries(query.data?.fields || {});
  const claims = query.data?.claims || [];

  function startFieldEdit(key: string, value: string) {
    setEditingField(key);
    setFieldValue(value);
  }

  function saveField(key: string) {
    const clean = fieldValue.trim();
    if (!clean) return;
    updateFieldMutation.mutate({ key, value: clean });
  }

  function startClaimEdit(id: number, claim: string, confidence: number) {
    setEditingClaim(id);
    setClaimValue(claim);
    setClaimConfidence(confidence);
  }

  function saveClaim(id: number) {
    const clean = claimValue.trim();
    if (!clean) return;
    updateClaimMutation.mutate({ id, claim: clean, confidence: claimConfidence });
  }

  return (
    <section className="view profile-view">
      <PageHeader
        description="画像是 MyBuddy 对你的稳定理解；字段偏确定，命题偏推断。"
        title="画像"
      />
      <div className="dashboard-grid">
        <Panel title="核心字段" description={`${fields.length} 个稳定字段`}>
          {fields.length ? (
            <div className="table-list">
              {fields.map(([key, value]) => (
                <article className="list-card field-card" key={key}>
                  <header>
                    <strong>{key}</strong>
                    <span>field</span>
                  </header>
                  {editingField === key ? (
                    <>
                      <input
                        aria-label={`${key} 的值`}
                        onChange={(event) => setFieldValue(event.target.value)}
                        value={fieldValue}
                      />
                      <div className="inline-actions">
                        <button
                          aria-label="保存字段"
                          className="icon-button"
                          data-state={updateFieldMutation.isPending ? "loading" : undefined}
                          disabled={updateFieldMutation.isPending || !fieldValue.trim()}
                          onClick={() => saveField(key)}
                          title="保存"
                          type="button"
                        >
                          <Check size={15} />
                        </button>
                        <CancelEditButton onClick={() => setEditingField(null)} label="取消编辑字段" />
                      </div>
                    </>
                  ) : (
                    <>
                      <p>{value}</p>
                      <div className="inline-actions">
                        <button
                          aria-label={`编辑${key}`}
                          className="icon-button"
                          onClick={() => startFieldEdit(key, value)}
                          title="编辑"
                          type="button"
                        >
                          <Pencil size={15} />
                        </button>
                        <ConfirmAction
                          confirmLabel="确认删除字段"
                          disabled={deleteFieldMutation.isPending}
                          label={`删除${key}`}
                          onConfirm={() => deleteFieldMutation.mutate(key)}
                          title="删除"
                        >
                          <Trash2 size={15} />
                        </ConfirmAction>
                      </div>
                    </>
                  )}
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无核心字段" text="字段通常来自明确资料或多轮稳定事实。" />
          )}
        </Panel>

        <Panel title="动态命题" description={`${claims.length} 条可校正推断`}>
          {claims.length ? (
            <div className="table-list">
              {claims.map((claim) => (
                <ClaimCard
                  claim={claim}
                  deleting={deleteClaimMutation.isPending}
                  editing={Boolean(claim.sql_id && editingClaim === claim.sql_id)}
                  key={`${claim.sql_id || claim.claim}`}
                  onCancel={() => setEditingClaim(null)}
                  onDelete={(id) => deleteClaimMutation.mutate(id)}
                  onEdit={(id) => startClaimEdit(id, claim.claim, claim.confidence || 0)}
                  onSave={(id) => saveClaim(id)}
                  saving={updateClaimMutation.isPending}
                  setClaimConfidence={setClaimConfidence}
                  setClaimValue={setClaimValue}
                  value={claimValue}
                  confidence={claimConfidence}
                />
              ))}
            </div>
          ) : (
            <EmptyState title="暂无动态命题" text="对话积累后，系统会在这里形成可修正推断。" />
          )}
        </Panel>
      </div>
    </section>
  );
}

function ClaimCard({
  claim,
  editing,
  saving,
  deleting,
  value,
  confidence,
  setClaimValue,
  setClaimConfidence,
  onEdit,
  onSave,
  onCancel,
  onDelete,
}: {
  claim: ProfileClaim;
  editing: boolean;
  saving: boolean;
  deleting: boolean;
  value: string;
  confidence: number;
  setClaimValue: (value: string) => void;
  setClaimConfidence: (value: number) => void;
  onEdit: (id: number) => void;
  onSave: (id: number) => void;
  onCancel: () => void;
  onDelete: (id: number) => void;
}) {
  const id = claim.sql_id;

  return (
    <article className="list-card claim-card">
      <header>
        <ConfidenceMeter value={claim.confidence || 0} />
        <span>观察</span>
      </header>
      {id && editing ? (
        <>
          <textarea aria-label="画像命题" onChange={(event) => setClaimValue(event.target.value)} rows={4} value={value} />
          <label className="compact-label">
            置信度
            <input
              max="1"
              min="0"
              onChange={(event) => setClaimConfidence(Number(event.target.value))}
              step="0.05"
              type="number"
              value={confidence}
            />
          </label>
          <div className="inline-actions">
            <button
              aria-label="保存画像命题"
              className="icon-button"
              data-state={saving ? "loading" : undefined}
              disabled={saving || !value.trim()}
              onClick={() => onSave(id)}
              title="保存"
              type="button"
            >
              <Check size={15} />
            </button>
            <CancelEditButton onClick={onCancel} label="取消编辑画像命题" />
          </div>
        </>
      ) : (
        <>
          <p>{claim.claim}</p>
          <div className="claim-meta">
            <span>{claimStatusLabel(claim.status)}</span>
            <span>{claimCategoryLabel(claim.category)}</span>
            {claim.evidence_count || claim.evidence_ids?.length ? (
              <span>{claim.evidence_count || claim.evidence_ids?.length} 条证据</span>
            ) : null}
          </div>
          {id ? (
            <div className="inline-actions">
              <button
                aria-label="编辑画像命题"
                className="icon-button"
                onClick={() => onEdit(id)}
                title="编辑"
                type="button"
              >
                <Pencil size={15} />
              </button>
              <ConfirmAction
                confirmLabel="确认删除命题"
                disabled={deleting}
                label="删除画像命题"
                onConfirm={() => onDelete(id)}
                title="删除"
              >
                <Trash2 size={15} />
              </ConfirmAction>
            </div>
          ) : null}
        </>
      )}
    </article>
  );
}

function claimStatusLabel(value?: string) {
  const labels: Record<string, string> = {
    candidate: "候选",
    active: "追踪中",
    stable: "稳定",
  };
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
