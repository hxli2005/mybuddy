import { Shield, ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";
import { cn } from "../lib/cn";

type Props = {
  onOpenCrisis: () => void;
};

export function SafetyDisclaimer({ onOpenCrisis }: Props) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="safety-bar">
      <div className="mx-auto w-full max-w-2xl px-4 sm:px-5">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="w-full flex items-center gap-2 py-1.5 text-[12px] text-ink-soft hover:text-ink transition-colors touch-target"
        >
          <Shield size={13} strokeWidth={1.8} className="shrink-0 text-positive" />
          <span className="flex-1 text-left">
            {expanded
              ? "MyBuddy 是心理健康陪伴工具，不能替代专业心理咨询、诊断或治疗。如果你正处于危机中，请立即联系紧急服务或拨打危机热线。"
              : "MyBuddy 不是治疗师 · 危机资源"}
          </span>
          {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </button>
        {expanded ? (
          <div className="pb-2 flex items-center gap-3 text-[12px]">
            <button
              type="button"
              onClick={onOpenCrisis}
              className="text-accent hover:underline touch-target inline-flex items-center gap-1"
            >
              <Shield size={12} />
              查看危机热线和资源
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
