import { useState } from "react";
import { LayoutDashboard, Users, Settings2, LogOut } from "lucide-react";
import { useAuth } from "../lib/auth";
import { cn } from "../lib/cn";
import { AdminDashboard } from "../components/AdminDashboard";
import { AdminUsers } from "../components/AdminUsers";
import { AdminConfig } from "../components/AdminConfig";

type AdminTab = "dashboard" | "users" | "config";

const tabDefs: Array<{ key: AdminTab; icon: typeof LayoutDashboard; label: string }> = [
  { key: "dashboard", icon: LayoutDashboard, label: "概览" },
  { key: "users", icon: Users, label: "用户管理" },
  { key: "config", icon: Settings2, label: "系统配置" },
];

export function AdminView() {
  const { isAdmin, logout } = useAuth();
  const [tab, setTab] = useState<AdminTab>("dashboard");

  if (!isAdmin) {
    return null;
  }

  return (
    <div className="h-dvh flex flex-col bg-bg overflow-hidden">
      <header className="sticky top-0 z-20 glass border-b border-line">
        <div className="mx-auto w-full max-w-5xl px-4 sm:px-5 h-14 flex items-center gap-3">
          <h1 className="text-[15px] font-semibold text-ink">管理面板</h1>
          <div className="flex-1" />
          <button
            type="button"
            onClick={logout}
            className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full text-[13px] font-medium text-muted hover:text-ink transition-colors"
            title="退出登录"
          >
            <LogOut size={14} strokeWidth={1.9} />
            退出
          </button>
          <div className="flex items-center gap-1">
            {tabDefs.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                className={cn(
                  "inline-flex items-center gap-1.5 h-8 px-3 rounded-full text-[13px] font-medium transition-colors",
                  tab === t.key ? "bg-accent-soft text-accent" : "text-muted hover:text-ink",
                )}
              >
                <t.icon size={14} strokeWidth={1.9} />
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="flex-1 min-h-0 overflow-y-auto">
        {tab === "dashboard" ? (
          <AdminDashboard />
        ) : tab === "users" ? (
          <AdminUsers />
        ) : (
          <AdminConfig />
        )}
      </main>

      {/* 移动端底部导航 */}
      <nav className="bottom-tab-bar sm:hidden">
        {tabDefs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={cn("bottom-tab-item touch-target", tab === t.key && "active")}
          >
            <t.icon size={20} strokeWidth={1.9} />
            <span>{t.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
