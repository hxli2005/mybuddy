import { useState } from "react";
import { Moon, Sun } from "lucide-react";
import { Sheet } from "./Sheet";
import { Chip, SectionLabel } from "./ui";
import { MentalHealthSection } from "../views/settings/MentalHealthSection";
import type { MentalHealthSettings } from "../views/settings/MentalHealthSection";

const defaultMentalSettings: MentalHealthSettings = {
  checkinReminder: true,
  cbtSuggestions: true,
  statusReminder: true,
  pipelineInsight: false,
};

export function loadMentalSettings(): MentalHealthSettings {
  try {
    const raw = localStorage.getItem("mybuddy-mental-settings");
    if (raw) return { ...defaultMentalSettings, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return defaultMentalSettings;
}

function saveMentalSettings(s: MentalHealthSettings) {
  try {
    localStorage.setItem("mybuddy-mental-settings", JSON.stringify(s));
  } catch { /* ignore */ }
}

type SettingsSheetProps = {
  open: boolean;
  onClose: () => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
};

export function SettingsSheet({ open, onClose, theme, onToggleTheme }: SettingsSheetProps) {
  const [mentalSettings, setMentalSettings] = useState<MentalHealthSettings>(loadMentalSettings);

  function handleMentalChange(s: MentalHealthSettings) {
    setMentalSettings(s);
    saveMentalSettings(s);
    // 通知已挂载的视图(如 ChatView)即时响应设置变化
    window.dispatchEvent(new CustomEvent("mybuddy:mental-settings", { detail: s }));
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="设置"
      subtitle="心理健康与偏好"
    >
      <div className="p-4 flex flex-col gap-5">
        <MentalHealthSection settings={mentalSettings} onChange={handleMentalChange} />

        <div className="flex flex-col gap-1.5">
          <SectionLabel>外观</SectionLabel>
          <button
            type="button"
            onClick={onToggleTheme}
            className="flex items-center gap-3 rounded-2xl border border-line bg-surface px-4 py-3.5 text-left hover:bg-surface-2 transition-colors"
          >
            <span className="grid place-items-center h-9 w-9 rounded-xl bg-surface-2 text-ink-soft">
              {theme === "dark" ? <Moon size={18} strokeWidth={1.9} /> : <Sun size={18} strokeWidth={1.9} />}
            </span>
            <span className="flex-1 min-w-0">
              <span className="block text-sm font-medium text-ink">主题</span>
              <span className="block text-[12.5px] text-muted">当前 {theme === "dark" ? "深色" : "浅色"}，点击切换</span>
            </span>
            <Chip tone="neutral">{theme === "dark" ? "深色" : "浅色"}</Chip>
          </button>
        </div>

        <p className="text-[11.5px] text-faint text-center pt-2">MyBuddy · 心理健康陪伴 · 本地运行</p>
      </div>
    </Sheet>
  );
}
