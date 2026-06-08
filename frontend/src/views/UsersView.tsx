import { Brain, Check, Link2, Plus, RotateCcw, RotateCw, UserCheck, UserX, X } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  bindQqAccount,
  createUser,
  fetchUserPersona,
  fetchUsers,
  resetUserPersona,
  saveUserPersona,
  updateUser,
} from "../api/client";
import { PersonaEditor } from "../components/PersonaEditor";
import { EmptyState, ErrorState, LoadingState, Metric, PageHeader, Panel, Tags } from "../components/Primitives";
import { queryKeys } from "../state/observability";
import type { ExternalAccount, Persona, TestUser, UserPersonaPayload } from "../types/api";

export function UsersView() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.users, queryFn: fetchUsers });
  const [displayName, setDisplayName] = useState("");
  const [dailyLimit, setDailyLimit] = useState("30");
  const [personaUserId, setPersonaUserId] = useState<number | null>(null);

  const users = query.data?.users ?? [];
  const personaUser = personaUserId === null ? null : users.find((user) => user.id === personaUserId) || null;
  const activeCount = useMemo(() => users.filter((user) => user.status === "active").length, [users]);
  const qqBindings = useMemo(
    () => users.reduce((count, user) => count + (user.external_accounts.some((account) => account.provider === "qq") ? 1 : 0), 0),
    [users],
  );
  const totalUsageToday = useMemo(
    () => users.reduce((count, user) => count + user.usage_total_today, 0),
    [users],
  );

  const createMutation = useMutation({
    mutationFn: createUser,
    onSuccess: () => {
      setDisplayName("");
      setDailyLimit("30");
      queryClient.invalidateQueries({ queryKey: queryKeys.users });
    },
  });

  function submitCreate(event: FormEvent) {
    event.preventDefault();
    const cleanName = displayName.trim();
    const limit = Number.parseInt(dailyLimit, 10);
    if (!cleanName || Number.isNaN(limit) || limit < 0) return;
    createMutation.mutate({
      display_name: cleanName,
      daily_message_limit: limit,
    });
  }

  if (query.isLoading) return <LoadingState label="正在读取测试用户" />;
  if (query.error) return <ErrorState error={query.error} />;

  return (
    <section className="view">
      <PageHeader
        eyebrow="小规模测试"
        title="测试用户"
        description="管理测试账号、QQ 绑定、启停状态和每日额度。"
        actions={
          <button className="text-button" onClick={() => query.refetch()} type="button">
            <RotateCw size={16} />
            <span>刷新</span>
          </button>
        }
      />

      <div className="users-dashboard">
        <Panel title="新建测试用户" description="创建后可绑定 QQ external_id。">
          <form className="user-create-form" onSubmit={submitCreate}>
            <label>
              显示名
              <input
                aria-label="测试用户显示名"
                onChange={(event) => setDisplayName(event.target.value)}
                value={displayName}
              />
            </label>
            <label>
              每日额度
              <input
                aria-label="每日额度"
                min={0}
                onChange={(event) => setDailyLimit(event.target.value)}
                type="number"
                value={dailyLimit}
              />
            </label>
            <button
              data-state={createMutation.isPending ? "loading" : undefined}
              disabled={createMutation.isPending || !displayName.trim()}
              type="submit"
            >
              <Plus size={16} />
              <span>{createMutation.isPending ? "创建中" : "创建用户"}</span>
            </button>
          </form>
          {createMutation.error ? <ErrorState error={createMutation.error} /> : null}
        </Panel>

        <Panel title="运行概览" description={`${users.length} 个测试用户`}>
          <div className="metric-grid">
            <Metric label="启用用户" value={activeCount} />
            <Metric label="QQ 绑定" value={qqBindings} />
            <Metric label="今日消息" value={totalUsageToday} />
          </div>
        </Panel>
      </div>

      {personaUser ? <UserPersonaPanel user={personaUser} onClose={() => setPersonaUserId(null)} /> : null}

      <Panel title="用户列表" description={`${users.length} 个账号`}>
        {users.length ? (
          <div className="users-list">
            {users.map((user) => (
              <UserCard key={user.id} onEditPersona={() => setPersonaUserId(user.id)} user={user} />
            ))}
          </div>
        ) : (
          <EmptyState title="暂无测试用户" text="创建第一个测试用户后，可在这里绑定 QQ 账号并控制额度。" />
        )}
      </Panel>
    </section>
  );
}

function UserCard({ user, onEditPersona }: { user: TestUser; onEditPersona: () => void }) {
  const queryClient = useQueryClient();
  const qqAccount = user.external_accounts.find((account) => account.provider === "qq");
  const [qqExternalId, setQqExternalId] = useState("");
  const [qqDisplayName, setQqDisplayName] = useState("");
  const [quotaInput, setQuotaInput] = useState(String(user.daily_message_limit));

  useEffect(() => {
    setQuotaInput(String(user.daily_message_limit));
  }, [user.daily_message_limit]);

  const refreshUsers = () => queryClient.invalidateQueries({ queryKey: queryKeys.users });
  const updateMutation = useMutation({
    mutationFn: ({ status, dailyLimit }: { status?: string; dailyLimit?: number }) =>
      updateUser(user.id, {
        status,
        daily_message_limit: dailyLimit,
      }),
    onSuccess: refreshUsers,
  });
  const bindMutation = useMutation({
    mutationFn: ({ externalId, displayName }: { externalId: string; displayName?: string }) =>
      bindQqAccount(user.id, {
        external_id: externalId,
        display_name: displayName,
      }),
    onSuccess: () => {
      setQqExternalId("");
      setQqDisplayName("");
      refreshUsers();
    },
  });

  const quota = Number.parseInt(quotaInput, 10);
  const quotaDirty = !Number.isNaN(quota) && quota !== user.daily_message_limit;
  const usageEntries = Object.entries(user.usage_today);
  const accountTags = user.external_accounts.map(accountTag);
  const active = user.status === "active";

  function submitBind(event: FormEvent) {
    event.preventDefault();
    const cleanExternalId = qqExternalId.trim();
    if (!cleanExternalId) return;
    bindMutation.mutate({
      externalId: cleanExternalId,
      displayName: qqDisplayName.trim() || undefined,
    });
  }

  function submitQuota(event: FormEvent) {
    event.preventDefault();
    if (Number.isNaN(quota) || quota < 0 || quota === user.daily_message_limit) return;
    updateMutation.mutate({ dailyLimit: quota });
  }

  function toggleStatus() {
    updateMutation.mutate({ status: active ? "disabled" : "active" });
  }

  return (
    <article className="list-card user-card">
      <header>
        <div>
          <strong>{user.display_name || `测试用户 #${user.id}`}</strong>
          <span>#{user.id}</span>
        </div>
        <div className="user-card-badges">
          <span className={active ? "badge active" : "badge disabled"}>{active ? "启用" : "停用"}</span>
          <span className={user.has_custom_persona ? "badge active" : "badge"}>{user.has_custom_persona ? "自定义人格" : "默认人格"}</span>
        </div>
      </header>

      <div className="user-card-grid">
        <div>
          <span>QQ 绑定</span>
          <strong>{qqAccount ? qqAccount.display_name || qqAccount.external_id : "未绑定"}</strong>
          {qqAccount ? <code className="user-account-id">{qqAccount.external_id}</code> : null}
        </div>
        <div>
          <span>今日用量</span>
          <strong>{formatQuota(user.usage_total_today, user.daily_message_limit)}</strong>
          {usageEntries.length ? (
            <div className="user-usage-tags">
              {usageEntries.map(([source, count]) => (
                <span className="tag" key={source}>
                  {sourceLabel(source)} {count}
                </span>
              ))}
            </div>
          ) : (
            <span>今天暂无消息</span>
          )}
        </div>
        <div>
          <span>人格</span>
          <strong>{user.has_custom_persona ? "自定义" : "继承默认"}</strong>
        </div>
        <div>
          <span>外部账号</span>
          {accountTags.length ? <Tags values={accountTags} /> : <strong>无</strong>}
        </div>
      </div>

      <div className="user-controls">
        <form className="user-inline-form" onSubmit={submitBind}>
          <label>
            QQ external_id
            <input
              aria-label={`用户 ${user.id} 的 QQ external_id`}
              onChange={(event) => setQqExternalId(event.target.value)}
              value={qqExternalId}
            />
          </label>
          <label>
            QQ 显示名
            <input
              aria-label={`用户 ${user.id} 的 QQ 显示名`}
              onChange={(event) => setQqDisplayName(event.target.value)}
              value={qqDisplayName}
            />
          </label>
          <button disabled={bindMutation.isPending || !qqExternalId.trim()} type="submit">
            <Link2 size={16} />
            <span>{qqAccount ? "改绑" : "绑定"}</span>
          </button>
        </form>

        <form className="user-inline-form quota-form" onSubmit={submitQuota}>
          <label>
            每日额度
            <input
              aria-label={`用户 ${user.id} 的每日额度`}
              min={0}
              onChange={(event) => setQuotaInput(event.target.value)}
              type="number"
              value={quotaInput}
            />
          </label>
          <button disabled={updateMutation.isPending || !quotaDirty} type="submit">
            <Check size={16} />
            <span>保存额度</span>
          </button>
          <button className="text-button" onClick={onEditPersona} type="button">
            <Brain size={16} />
            <span>人格</span>
          </button>
          <button
            aria-pressed={active}
            className="text-button"
            disabled={updateMutation.isPending}
            onClick={toggleStatus}
            type="button"
          >
            {active ? <UserX size={16} /> : <UserCheck size={16} />}
            <span>{active ? "停用" : "启用"}</span>
          </button>
        </form>
      </div>

      {bindMutation.error || updateMutation.error ? (
        <p className="user-error">{errorMessage(bindMutation.error || updateMutation.error)}</p>
      ) : null}
    </article>
  );
}

function UserPersonaPanel({ user, onClose }: { user: TestUser; onClose: () => void }) {
  const queryClient = useQueryClient();
  const queryKey = queryKeys.userPersona(user.id);
  const query = useQuery({
    queryKey,
    queryFn: () => fetchUserPersona(user.id),
  });

  const refresh = (payload: UserPersonaPayload) => {
    queryClient.setQueryData(queryKey, payload);
    queryClient.invalidateQueries({ queryKey: queryKeys.users });
  };
  const saveMutation = useMutation({
    mutationFn: (persona: Persona) => saveUserPersona(user.id, persona),
    onSuccess: refresh,
  });
  const resetMutation = useMutation({
    mutationFn: () => resetUserPersona(user.id),
    onSuccess: refresh,
  });

  return (
    <section className="user-persona-section">
      <header className="panel-header">
        <div>
          <h3>用户人格</h3>
          <p>
            {user.display_name || `测试用户 #${user.id}`} ·{" "}
            {query.data?.inherits_default ? "继承全局默认" : "使用自定义人格"}
          </p>
        </div>
        <div className="inline-actions">
          <button
            className="text-button"
            disabled={query.isLoading || query.data?.inherits_default || resetMutation.isPending}
            onClick={() => resetMutation.mutate()}
            type="button"
          >
            <RotateCcw size={16} />
            <span>{resetMutation.isPending ? "重置中" : "重置默认"}</span>
          </button>
          <button aria-label="关闭用户人格编辑" className="icon-button" onClick={onClose} title="关闭" type="button">
            <X size={16} />
          </button>
        </div>
      </header>

      {query.isLoading ? <div className="muted-row">正在读取用户人格</div> : null}
      {query.error ? <ErrorState error={query.error} /> : null}
      {query.data ? (
        <PersonaEditor
          error={saveMutation.error || resetMutation.error}
          formId={`user-persona-form-${user.id}`}
          onSave={(persona) => saveMutation.mutate(persona)}
          persona={query.data.persona}
          saving={saveMutation.isPending}
        />
      ) : null}
    </section>
  );
}

function accountTag(account: ExternalAccount) {
  return `${account.provider}:${account.external_id}`;
}

function formatQuota(used: number, limit: number) {
  if (limit <= 0) return `${used}/不限`;
  return `${used}/${limit}`;
}

function sourceLabel(source: string) {
  if (source === "qq") return "QQ";
  if (source === "web") return "Web";
  if (source === "chat") return "Chat";
  return source;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
