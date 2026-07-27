import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { updateAdminUser, deleteAdminUser, resetUserPassword } from "../lib/api";
import { Sheet } from "./Sheet";
import { Button, Chip, Field, Input, Surface } from "./ui";
import type { AdminUser } from "../types/api";
import { useAuth } from "../lib/auth";

type Props = {
  user: AdminUser;
  onClose: () => void;
  onUpdated: () => void;
};

export function AdminUserDetail({ user, onClose, onUpdated }: Props) {
  const { userId } = useAuth();
  const queryClient = useQueryClient();
  const [quotaStr, setQuotaStr] = useState(
    user.daily_message_limit === 0 ? "" : String(user.daily_message_limit),
  );

  const updateMutation = useMutation({
    mutationFn: (updates: { status?: string; daily_message_limit?: number; role?: string; display_name?: string }) =>
      updateAdminUser(user.id, updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["adminUsers"] });
      onUpdated();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteAdminUser(user.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["adminUsers"] });
      onClose();
    },
    onError: (err) => alert(err instanceof Error ? err.message : String(err)),
  });

  const resetPwdMutation = useMutation({
    mutationFn: () => resetUserPassword(user.id),
    onSuccess: () => alert("密码已重置为 123456"),
    onError: (err) => alert(err instanceof Error ? err.message : String(err)),
  });

  function handleDelete() {
    if (!window.confirm(`确定要删除用户「${user.display_name}」(ID: ${user.id}) 吗？此操作不可撤销。`)) return;
    deleteMutation.mutate();
  }

  function handleResetPassword() {
    if (!window.confirm(`确定要重置「${user.display_name}」的密码为 123456 吗？`)) return;
    resetPwdMutation.mutate();
  }

  function handleSetQuota() {
    const v = quotaStr.trim();
    const n = v === "" ? 0 : parseInt(v, 10);
    if (isNaN(n) || n < 0) return;
    updateMutation.mutate({ daily_message_limit: n });
  }

  return (
    <Sheet open onClose={onClose} title="用户详情" subtitle={`ID: ${user.id}`} side="right">
      <div className="flex flex-col gap-4">
        {/* 基本信息 */}
        <Surface className="p-4 flex flex-col gap-2">
          <h3 className="text-[12px] font-medium text-muted tracking-wide">基本信息</h3>
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-medium text-ink">{user.display_name}</span>
            <Chip
              tone={user.role === "admin" ? "accent" : "neutral"}
            >
              {user.role === "admin" ? "管理员" : "用户"}
            </Chip>
            <Chip
              tone={user.status === "active" ? "positive" : "negative"}
            >
              {user.status === "active" ? "正常" : "禁用"}
            </Chip>
          </div>
          <div className="text-[12px] text-muted flex flex-col gap-0.5">
            <span>注册时间: {user.created_at ?? "-"}</span>
            <span>最近更新: {user.updated_at ?? "-"}</span>
            <span>访客账号: {user.is_guest ? "是" : "否"}</span>
          </div>
        </Surface>

        {/* 快捷操作 */}
        <Surface className="p-4 flex flex-col gap-3">
          <h3 className="text-[12px] font-medium text-muted tracking-wide">快捷操作</h3>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="secondary"
              onClick={() =>
                updateMutation.mutate({
                  status: user.status === "active" ? "disabled" : "active",
                })
              }
              disabled={updateMutation.isPending}
            >
              {user.status === "active" ? "禁用账户" : "启用账户"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() =>
                updateMutation.mutate({
                  role: user.role === "admin" ? "user" : "admin",
                })
              }
              disabled={updateMutation.isPending}
            >
              {user.role === "admin" ? "降级为用户" : "提升为管理员"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={handleResetPassword}
              disabled={resetPwdMutation.isPending}
            >
              重置密码
            </Button>
            {user.id !== userId && (
              <Button
                size="sm"
                variant="secondary"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
                className="text-negative hover:text-negative"
              >
                删除账户
              </Button>
            )}
          </div>
          <Field label="每日消息配额 (0 = 不限)">
            <div className="flex items-center gap-2">
              <Input
                value={quotaStr}
                onChange={(e) => setQuotaStr((e.target as HTMLInputElement).value)}
                placeholder="0"
                className="w-24"
              />
              <Button size="sm" variant="primary" onClick={handleSetQuota} disabled={updateMutation.isPending} className="whitespace-nowrap shrink-0">
                保存
              </Button>
            </div>
          </Field>
        </Surface>

        {/* 今日用量 */}
        <Surface className="p-4 flex flex-col gap-2">
          <h3 className="text-[12px] font-medium text-muted tracking-wide">今日用量</h3>
          {Object.keys(user.usage_today).length === 0 ? (
            <span className="text-[13px] text-muted">暂无使用记录</span>
          ) : (
            <div className="flex flex-col gap-1">
              {Object.entries(user.usage_today).map(([source, count]) => (
                <div key={source} className="flex items-center justify-between text-[13px]">
                  <span className="text-ink">{source}</span>
                  <span className="text-muted tabular-nums">{count}</span>
                </div>
              ))}
              <div className="border-t border-line pt-1 flex items-center justify-between text-[13px] font-medium">
                <span className="text-ink">合计</span>
                <span className="text-ink tabular-nums">{user.usage_total_today}</span>
              </div>
            </div>
          )}
        </Surface>

        {/* 外部绑定 */}
        {user.external_accounts.length > 0 && (
          <Surface className="p-4 flex flex-col gap-2">
            <h3 className="text-[12px] font-medium text-muted tracking-wide">外部账号绑定</h3>
            {user.external_accounts.map((a, i) => (
              <div key={i} className="text-[13px] text-ink flex items-center gap-2">
                <Chip tone="neutral">{a.provider}</Chip>
                <span>{a.display_name || a.external_id}</span>
              </div>
            ))}
          </Surface>
        )}
      </div>
    </Sheet>
  );
}
