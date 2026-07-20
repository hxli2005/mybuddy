import { Shield } from "lucide-react";

type Props = {
  onOpenCrisis: () => void;
};

export function ChatCrisisBanner({ onOpenCrisis }: Props) {
  return (
    <div className="crisis-banner rounded-2xl px-4 py-3 animate-fade-in">
      <div className="flex items-start gap-2.5">
        <Shield size={16} strokeWidth={1.8} className="text-negative shrink-0 mt-0.5" />
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-ink">
            我现在很关心你
          </p>
          <p className="text-[12px] text-ink-soft mt-0.5">
            这里有一些可能有帮助的资源。你不需要独自面对。
          </p>
          <button
            type="button"
            onClick={onOpenCrisis}
            className="inline-flex items-center gap-1 mt-2 text-[12.5px] font-medium text-accent hover:underline"
          >
            <Shield size={12} />
            查看危机热线和资源
          </button>
        </div>
      </div>
    </div>
  );
}
