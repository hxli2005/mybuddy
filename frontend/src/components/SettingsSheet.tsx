import { useEffect, useState } from "react";
import {
  Bell,
  Brain,
  ChevronLeft,
  ChevronRight,
  Moon,
  NotebookText,
  Sparkles,
  Sun,
  Wand2,
  type LucideIcon,
} from "lucide-react";
import { Sheet } from "./Sheet";
import { Chip, IconButton, SectionLabel } from "./ui";
import { cn } from "../lib/cn";
import { PersonaSection } from "../views/settings/PersonaSection";
import { MemorySection } from "../views/settings/MemorySection";
import { RemindersSection } from "../views/settings/RemindersSection";
import { NotesSection } from "../views/settings/NotesSection";
import { SkillsSection } from "../views/settings/SkillsSection";

type Section = "persona" | "memory" | "reminders" | "notes" | "skills";

const sectionMeta: Record<Section, { label: string; desc: string; icon: LucideIcon }> = {
  persona: { label: "小布是谁", desc: "名字、语气、和你的关系", icon: Sparkles },
  memory: { label: "TA 记得你", desc: "画像与长期记忆", icon: Brain },
  reminders: { label: "提醒", desc: "定时与待播消息", icon: Bell },
  notes: { label: "笔记", desc: "随手记下的材料", icon: NotebookText },
  skills: { label: "TA 学会的", desc: "积累的应对习惯", icon: Wand2 },
};

const sectionOrder: Section[] = ["persona", "memory", "reminders", "notes", "skills"];

type SettingsSheetProps = {
  open: boolean;
  onClose: () => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
};

export function SettingsSheet({ open, onClose, theme, onToggleTheme }: SettingsSheetProps) {
  const [section, setSection] = useState<Section | null>(null);

  useEffect(() => {
    if (!open) {
      const t = setTimeout(() => setSection(null), 320);
      return () => clearTimeout(t);
    }
  }, [open]);

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={section ? sectionMeta[section].label : "设置"}
      subtitle={section ? sectionMeta[section].desc : "小布和你的小世界"}
      actions={section ? <IconButton icon={ChevronLeft} label="返回" onClick={() => setSection(null)} /> : undefined}
    >
      {section ? (
        <div className="p-4">
          {section === "persona" && <PersonaSection />}
          {section === "memory" && <MemorySection />}
          {section === "reminders" && <RemindersSection />}
          {section === "notes" && <NotesSection />}
          {section === "skills" && <SkillsSection />}
        </div>
      ) : (
        <div className="p-4 flex flex-col gap-5">
          <div className="flex flex-col gap-1.5">
            <SectionLabel>小布</SectionLabel>
            <div className="rounded-2xl border border-line bg-surface overflow-hidden">
              {sectionOrder.map((id, i) => (
                <SectionRow
                  key={id}
                  icon={sectionMeta[id].icon}
                  label={sectionMeta[id].label}
                  desc={sectionMeta[id].desc}
                  divider={i > 0}
                  onClick={() => setSection(id)}
                />
              ))}
            </div>
          </div>

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

          <p className="text-[11.5px] text-faint text-center pt-2">MyBuddy · 生活陪伴 · 本地运行</p>
        </div>
      )}
    </Sheet>
  );
}

function SectionRow({
  icon: Icon,
  label,
  desc,
  onClick,
  divider,
}: {
  icon: LucideIcon;
  label: string;
  desc: string;
  onClick: () => void;
  divider?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-3 px-4 py-3.5 text-left hover:bg-surface-2 transition-colors",
        divider && "border-t border-line",
      )}
    >
      <span className="grid place-items-center h-9 w-9 rounded-xl bg-accent-soft text-accent">
        <Icon size={18} strokeWidth={1.9} />
      </span>
      <span className="flex-1 min-w-0">
        <span className="block text-sm font-medium text-ink">{label}</span>
        <span className="block text-[12.5px] text-muted truncate">{desc}</span>
      </span>
      <ChevronRight size={18} className="text-faint shrink-0" />
    </button>
  );
}
