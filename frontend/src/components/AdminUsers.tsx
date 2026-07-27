import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Search, MoreHorizontal, Trash2, KeyRound } from "lucide-react";
import { fetchAdminUsers, updateAdminUser, deleteAdminUser, batchSetQuota, resetUserPassword } from "../lib/api";
import { Surface, Button, Chip, Spinner, EmptyState, Input } from "./ui";
import { AdminUserDetail } from "./AdminUserDetail";
import type { AdminUser } from "../types/api";

export function AdminUsers() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [batchQuota, setBatchQuota] = useState("");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["adminUsers"],
    queryFn: fetchAdminUsers,
    staleTime: 0,
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, updates }: { id: number; updates: { status?: string; daily_message_limit?: number; role?: string } }) =>
      updateAdminUser(id, updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["adminUsers"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteAdminUser(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["adminUsers"] });
    },
  });

  const batchQuotaMutation = useMutation({
    mutationFn: ({ ids, limit }: { ids: number[]; limit: number }) => batchSetQuota(ids, limit),
    onSuccess: (data) => {
      alert(`已更新 ${data.updated} 个用户的配额`);
      setSelectedIds(new Set());
      setBatchQuota("");
      queryClient.invalidateQueries({ queryKey: ["adminUsers"] });
    },
    onError: (err) => alert(err instanceof Error ? err.message : String(err)),
  });

  const resetPasswordMutation = useMutation({
    mutationFn: (id: number) => resetUserPassword(id),
    onSuccess: () => alert("密码已重置为 123456"),
    onError: (err) => alert(err instanceof Error ? err.message : String(err)),
  });

  function handleDelete(user: AdminUser) {
    if (!window.confirm(`确定要删除用户「${user.display_name}」(ID: ${user.id}) 吗？此操作不可撤销。`)) return;
    deleteMutation.mutate(user.id, {
      onError: (err) => alert(err instanceof Error ? err.message : String(err)),
    });
  }

  function handleResetPassword(user: AdminUser) {
    if (!window.confirm(`确定要重置「${user.display_name}」的密码为 123456 吗？`)) return;
    resetPasswordMutation.mutate(user.id);
  }

  const toggleSelect = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  function handleBatchQuota() {
    const v = batchQuota.trim();
    const n = v === "" ? 0 : parseInt(v, 10);
    if (isNaN(n) || n < 0) { alert("请输入有效的配额数值"); return; }
    batchQuotaMutation.mutate({ ids: [...selectedIds], limit: n });
  }

  if (isLoading) {
    return (
      <div className="mx-auto w-full max-w-5xl px-4 py-12 flex justify-center">
        <Spinner />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mx-auto w-full max-w-5xl px-4 py-12">
        <EmptyState title="加载失败" text="无法获取用户列表" />
      </div>
    );
  }

  const users = data?.users ?? [];
  const filtered = search
    ? users.filter(
        (u) =>
          u.display_name.toLowerCase().includes(search.toLowerCase()) ||
          String(u.id).includes(search),
      )
    : users;
  const allSelected = filtered.length > 0 && selectedIds.size === filtered.length;

  function toggleSelectAll() {
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filtered.map((u) => u.id)));
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6 flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <h2 className="text-[13px] font-medium text-muted tracking-wide">用户管理</h2>
        <span className="text-[12px] text-muted">{users.length} 个用户</span>
      </div>

      {/* 搜索 + 批量操作栏 */}
      <div className="flex flex-col gap-2">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <Input
            value={search}
            onChange={(e) => setSearch((e.target as HTMLInputElement).value)}
            placeholder="搜索用户名或 ID…"
            className="pl-9"
          />
        </div>
        {selectedIds.size > 0 && (
          <Surface className="p-3 flex items-center gap-3 flex-wrap">
            <span className="text-[13px] text-muted whitespace-nowrap">
              已选 {selectedIds.size} 个用户
            </span>
            <Input
              value={batchQuota}
              onChange={(e) => setBatchQuota((e.target as HTMLInputElement).value)}
              placeholder="新配额 (0=不限)"
              className="w-36"
            />
            <Button
              size="sm"
              variant="primary"
              onClick={handleBatchQuota}
              disabled={batchQuotaMutation.isPending}
              className="whitespace-nowrap"
            >
              批量设置
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => { setSelectedIds(new Set()); setBatchQuota(""); }}
            >
              取消选择
            </Button>
          </Surface>
        )}
      </div>

      {filtered.length === 0 ? (
        <EmptyState title="无匹配用户" text={search ? "尝试其他关键词" : "暂无注册用户"} />
      ) : (
        <>
          {/* PC 端表格 */}
          <div className="hidden sm:block overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-line text-muted text-left">
                  <th className="pb-2 pr-2 w-8 font-medium">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleSelectAll}
                      className="size-3.5 accent-accent"
                    />
                  </th>
                  <th className="pb-2 pr-3 font-medium">ID</th>
                  <th className="pb-2 pr-3 font-medium">用户名</th>
                  <th className="pb-2 pr-3 font-medium">角色</th>
                  <th className="pb-2 pr-3 font-medium">状态</th>
                  <th className="pb-2 pr-3 font-medium">配额</th>
                  <th className="pb-2 pr-3 font-medium">今日用量</th>
                  <th className="pb-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((user) => (
                  <tr key={user.id} className="border-b border-line/60 hover:bg-surface-2/50 transition-colors">
                    <td className="py-2.5 pr-2">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(user.id)}
                        onChange={() => toggleSelect(user.id)}
                        className="size-3.5 accent-accent"
                      />
                    </td>
                    <td className="py-2.5 pr-3 text-muted tabular-nums">{user.id}</td>
                    <td className="py-2.5 pr-3 font-medium text-ink">{user.display_name}</td>
                    <td className="py-2.5 pr-3">
                      <Chip tone={user.role === "admin" ? "accent" : "neutral"}>
                        {user.role === "admin" ? "管理员" : "用户"}
                      </Chip>
                    </td>
                    <td className="py-2.5 pr-3">
                      <Chip tone={user.status === "active" ? "positive" : "negative"}>
                        {user.status === "active" ? "正常" : "禁用"}
                      </Chip>
                    </td>
                    <td className="py-2.5 pr-3 tabular-nums text-muted">
                      {user.daily_message_limit === 0 ? "不限" : user.daily_message_limit}
                    </td>
                    <td className="py-2.5 pr-3 tabular-nums text-muted">{user.usage_total_today}</td>
                    <td className="py-2.5 flex items-center gap-1.5">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          updateMutation.mutate({
                            id: user.id,
                            updates: { status: user.status === "active" ? "disabled" : "active" },
                          })
                        }
                        disabled={updateMutation.isPending}
                      >
                        {user.status === "active" ? "禁用" : "启用"}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleResetPassword(user)}
                        disabled={resetPasswordMutation.isPending}
                        title="重置密码为 123456"
                      >
                        <KeyRound size={14} strokeWidth={1.7} />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setSelected(user)}>
                        <MoreHorizontal size={14} strokeWidth={1.7} />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleDelete(user)}
                        disabled={deleteMutation.isPending}
                        className="text-negative hover:text-negative"
                      >
                        <Trash2 size={14} strokeWidth={1.7} />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 移动端卡片 */}
          <div className="sm:hidden flex flex-col gap-2">
            {selectedIds.size > 0 && (
              <div className="flex items-center gap-2">
                <span className="text-[12px] text-muted">已选 {selectedIds.size} 个</span>
                <Button size="sm" variant="ghost" onClick={() => setSelectedIds(new Set())}>
                  取消
                </Button>
              </div>
            )}
            {filtered.map((user) => (
              <Surface key={user.id} className="p-3 flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(user.id)}
                    onChange={() => toggleSelect(user.id)}
                    className="size-3.5 accent-accent"
                  />
                  <span className="font-medium text-ink">{user.display_name}</span>
                  <span className="text-[12px] text-muted">ID {user.id}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Chip tone={user.role === "admin" ? "accent" : "neutral"}>
                    {user.role === "admin" ? "管理员" : "用户"}
                  </Chip>
                  <Chip tone={user.status === "active" ? "positive" : "negative"}>
                    {user.status === "active" ? "正常" : "禁用"}
                  </Chip>
                </div>
                <div className="flex items-center gap-3 text-[12px] text-muted">
                  <span>配额: {user.daily_message_limit === 0 ? "不限" : user.daily_message_limit}</span>
                  <span>今日: {user.usage_total_today}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      updateMutation.mutate({
                        id: user.id,
                        updates: { status: user.status === "active" ? "disabled" : "active" },
                      })
                    }
                    disabled={updateMutation.isPending}
                  >
                    {user.status === "active" ? "禁用" : "启用"}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleResetPassword(user)}
                    disabled={resetPasswordMutation.isPending}
                  >
                    重置密码
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setSelected(user)}>
                    详情
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleDelete(user)}
                    disabled={deleteMutation.isPending}
                    className="text-negative hover:text-negative"
                  >
                    删除
                  </Button>
                </div>
              </Surface>
            ))}
          </div>
        </>
      )}

      {selected && (
        <AdminUserDetail
          user={selected}
          onClose={() => setSelected(null)}
          onUpdated={() => {
            setSelected(null);
            queryClient.invalidateQueries({ queryKey: ["adminUsers"] });
          }}
        />
      )}
    </div>
  );
}
