import { useState } from "react";
import { Smile, Frown, Meh, Annoyed, Laugh, Heart } from "lucide-react";
import { Button, Surface, Textarea } from "./ui";
import { cn } from "../lib/cn";

type Props = {
  open: boolean;
  onClose: () => void;
  onSubmit: (score: number, notes: string) => void;
  loading?: boolean;
};

const moods = [
  { score: 9, icon: Laugh, label: "很棒", color: "text-positive" },
  { score: 7, icon: Smile, label: "不错", color: "text-positive/70" },
  { score: 5, icon: Meh, label: "一般", color: "text-muted" },
  { score: 3, icon: Annoyed, label: "不太好", color: "text-negative/70" },
  { score: 1, icon: Frown, label: "很糟糕", color: "text-negative" },
];

export function CheckInDialog({ open, onClose, onSubmit, loading }: Props) {
  const [selected, setSelected] = useState<number | null>(null);
  const [notes, setNotes] = useState("");

  if (!open) return null;

  function handleSubmit() {
    if (selected == null) return;
    onSubmit(selected, notes);
    setSelected(null);
    setNotes("");
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/35 backdrop-blur-[2px]" />
      <Surface
        className="relative w-full sm:max-w-sm rounded-b-none sm:rounded-2xl p-5 animate-rise max-sm:pb-[calc(1.25rem+env(safe-area-inset-bottom,0px))]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-center mb-4">
          <Heart size={22} strokeWidth={1.8} className="text-accent mx-auto mb-2" />
          <p className="font-semibold text-ink">今天感觉怎么样？</p>
        </div>

        <div className="flex justify-center gap-2 mb-4">
          {moods.map((m) => (
            <button
              key={m.score}
              type="button"
              onClick={() => setSelected(m.score)}
              className={cn(
                "flex flex-col items-center gap-1 p-2 rounded-xl transition-all touch-target",
                selected === m.score
                  ? "bg-accent-soft scale-110"
                  : "hover:bg-surface-2",
              )}
            >
              <m.icon size={28} strokeWidth={1.6} className={m.color} />
              <span className="text-[10.5px] text-muted">{m.label}</span>
            </button>
          ))}
        </div>

        <Textarea
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="想写点什么…（选填）"
          className="mb-4"
        />

        <Button
          onClick={handleSubmit}
          disabled={selected == null || loading}
          className="w-full"
        >
          {loading ? "记录中…" : "签到"}
        </Button>
      </Surface>
    </div>
  );
}
