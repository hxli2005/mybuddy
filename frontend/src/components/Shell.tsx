import { useState } from "react";
import type { ReactNode } from "react";
import { Moon, Settings2, Sun } from "lucide-react";
import { Avatar, IconButton } from "./ui";
import { SettingsSheet } from "./SettingsSheet";
import type { Presence } from "../lib/queryKeys";

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

type ShellProps = {
  presence: Presence;
  children: ReactNode;
  settingsOpen: boolean;
  onOpenSettings: () => void;
  onCloseSettings: () => void;
};

export function Shell({ presence, children, settingsOpen, onOpenSettings, onCloseSettings }: ShellProps) {
  const { theme, toggle } = useTheme();

  return (
    <div className="h-dvh flex flex-col bg-bg">
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
          <IconButton icon={theme === "dark" ? Sun : Moon} label="切换主题" onClick={toggle} />
          <IconButton icon={Settings2} label="设置" onClick={onOpenSettings} />
        </div>
      </header>

      <main className="flex-1 min-h-0">{children}</main>

      <SettingsSheet open={settingsOpen} onClose={onCloseSettings} theme={theme} onToggleTheme={toggle} />
    </div>
  );
}
