import { useState, type ReactNode } from "react";
import { MessageCircle, User as UserIcon, Moon, Sun, Shield, Settings2 } from "lucide-react";
import { Avatar, IconButton } from "./ui";
import { SettingsSheet } from "./SettingsSheet";
import { UserMenu } from "./UserMenu";
import { CrisisPanel } from "./CrisisPanel";
import { useRouter, type Page } from "../lib/router";
import type { Presence } from "../lib/queryKeys";
import { cn } from "../lib/cn";

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    typeof document !== "undefined" && document.documentElement.getAttribute("data-theme") === "dark"
      ? "dark"
      : "light",
  );
  function toggle() {
    setTheme((current) => {
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try {
        localStorage.setItem("mybuddy-theme", next);
      } catch {
        /* ignore */
      }
      return next;
    });
  }
  return { theme, toggle };
}

const presenceText: Record<Presence, string> = {
  calm: "在听你说",
  positive: "心情不错",
  negative: "我在，慢慢说",
};

const presenceDot: Record<Presence, string> = {
  calm: "bg-accent",
  positive: "bg-positive",
  negative: "bg-negative",
};

const tabs: Array<{ page: Page; icon: typeof MessageCircle; label: string }> = [
  { page: "chat", icon: MessageCircle, label: "聊天" },
  { page: "mood", icon: UserIcon, label: "我的" },
];

type ShellProps = {
  presence: Presence;
  children: ReactNode;
  settingsOpen: boolean;
  onOpenSettings: () => void;
  onCloseSettings: () => void;
  crisisOpen: boolean;
  onOpenCrisis: () => void;
  onCloseCrisis: () => void;
};

export function Shell({ presence, children, settingsOpen, onOpenSettings, onCloseSettings, crisisOpen, onOpenCrisis, onCloseCrisis }: ShellProps) {
  const { theme, toggle } = useTheme();
  const { page, navigate } = useRouter();

  return (
    <div className="flex-1 min-h-0 flex flex-col bg-bg">
      {/* safety disclaimer is rendered by App.tsx, above Shell */}

      <header className="sticky top-0 z-20 glass border-b border-line">
        <div className="mx-auto w-full max-w-2xl px-4 sm:px-5 h-16 flex items-center gap-3">
          <Avatar size={38} presence={presence} />
          <div className="min-w-0 flex-1 leading-tight">
            <p className="font-semibold text-ink">小布</p>
            <p className="text-[12px] text-muted flex items-center gap-1.5">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${presenceDot[presence]}`} />
              {presenceText[presence]}
            </p>
          </div>
          {/* PC 端导航 */}
          <div className="hidden sm:flex items-center gap-1 mr-1">
            {tabs.map((t) => (
              <button
                key={t.page}
                type="button"
                onClick={() => navigate(t.page)}
                className={cn(
                  "inline-flex items-center gap-1.5 h-9 px-3 rounded-full text-[13px] font-medium transition-colors",
                  page === t.page ? "bg-accent-soft text-accent" : "text-muted hover:text-ink",
                )}
              >
                <t.icon size={15} strokeWidth={1.9} />
                {t.label}
              </button>
            ))}
          </div>
          <IconButton icon={Shield} label="危机资源" onClick={onOpenCrisis} tone="accent" />
          <UserMenu />
          <IconButton icon={theme === "dark" ? Sun : Moon} label="切换主题" onClick={toggle} />
          <IconButton icon={Settings2} label="设置" onClick={onOpenSettings} />
        </div>
      </header>

      <main className="flex-1 min-h-0">{children}</main>

      {/* 移动端底部导航 */}
      <nav className="bottom-tab-bar sm:hidden">
        {tabs.map((t) => (
          <button
            key={t.page}
            type="button"
            onClick={() => navigate(t.page)}
            className={cn("bottom-tab-item touch-target", page === t.page && "active")}
          >
            <t.icon size={22} strokeWidth={1.9} />
            <span>{t.label}</span>
          </button>
        ))}
      </nav>

      <SettingsSheet open={settingsOpen} onClose={onCloseSettings} theme={theme} onToggleTheme={toggle} />
      <CrisisPanel open={crisisOpen} onClose={onCloseCrisis} />
    </div>
  );
}
