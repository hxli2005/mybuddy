import { useQuery } from "@tanstack/react-query";
import { Users, UserCheck, Shield, UserX, MessageCircle, TrendingUp } from "lucide-react";
import { fetchAdminStats } from "../lib/api";
import { Surface, Spinner, EmptyState } from "./ui";

const statCards = [
  { key: "total_users", label: "总用户", icon: Users },
  { key: "active_users", label: "活跃用户", icon: UserCheck },
  { key: "admin_users", label: "管理员", icon: Shield },
  { key: "disabled_users", label: "已禁用", icon: UserX },
  { key: "total_messages", label: "总消息", icon: MessageCircle },
  { key: "messages_today", label: "今日消息", icon: TrendingUp },
] as const;

export function AdminDashboard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["adminStats"],
    queryFn: fetchAdminStats,
    staleTime: 0,
  });

  if (isLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-12 flex justify-center">
        <Spinner />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-12">
        <EmptyState title="加载失败" text="无法获取系统统计数据" />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-6 flex flex-col gap-5">
      <h2 className="text-[13px] font-medium text-muted tracking-wide">系统概览</h2>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {statCards.map((card) => {
          const Icon = card.icon;
          const value = data[card.key as keyof typeof data] ?? 0;
          return (
            <Surface key={card.key} className="p-4 flex flex-col gap-1.5">
              <div className="flex items-center gap-2 text-muted">
                <Icon size={15} strokeWidth={1.7} />
                <span className="text-[12px]">{card.label}</span>
              </div>
              <span className="text-2xl font-semibold text-ink tabular-nums">
                {typeof value === "number" ? value.toLocaleString() : value}
              </span>
            </Surface>
          );
        })}
      </div>
      {data.total_usage_today > 0 && (
        <Surface className="p-4">
          <span className="text-[12px] text-muted">今日总用量</span>
          <span className="text-xl font-semibold text-ink ml-2 tabular-nums">
            {data.total_usage_today.toLocaleString()}
          </span>
        </Surface>
      )}
    </div>
  );
}
