import { Wand2 } from "lucide-react";

type CbtPromptData = {
  technique?: string;
  title?: string;
  description?: string;
};

type Props = {
  data: CbtPromptData | null;
};

const defaultData: CbtPromptData = {
  technique: "小练习",
  title: "一个互动小练习",
  description: "跟着感觉走就好",
};

export function ChatCbtPrompt({ data }: Props) {
  const d = data || defaultData;

  return (
    <div className="cbt-prompt-card rounded-2xl px-4 py-3 mt-1.5 max-w-[85%] w-full">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="grid place-items-center h-6 w-6 rounded-full bg-accent/15 text-accent">
          <Wand2 size={13} strokeWidth={1.8} />
        </span>
        <span className="text-[11.5px] font-semibold uppercase tracking-wide text-accent">
          + {d.technique}
        </span>
      </div>
      <p className="text-[13px] font-medium text-ink">{d.title}</p>
      {d.description ? (
        <p className="text-[12px] text-ink-soft mt-0.5">{d.description}</p>
      ) : null}
    </div>
  );
}
