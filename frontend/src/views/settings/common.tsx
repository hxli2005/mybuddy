import { useState } from "react";
import type { ReactNode } from "react";
import { Trash2, X } from "lucide-react";
import { Spinner } from "../../components/ui";

export function SectionState({
  loading,
  error,
  children,
}: {
  loading?: boolean;
  error?: unknown;
  children: ReactNode;
}) {
  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-12 text-muted text-sm">
        <Spinner /> 读取中…
      </div>
    );
  }
  if (error) {
    return (
      <p className="text-[13px] text-negative bg-negative/10 rounded-xl px-3.5 py-2.5">
        {error instanceof Error ? error.message : String(error)}
      </p>
    );
  }
  return <>{children}</>;
}

export function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <span className="inline-flex items-center gap-2">
      <span className="h-1.5 w-14 rounded-full bg-surface-2 overflow-hidden">
        <span className="block h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </span>
      <span className="text-[11px] tabular-nums text-faint">{pct}%</span>
    </span>
  );
}

export function ConfirmDelete({
  onConfirm,
  label = "删除",
  disabled,
}: {
  onConfirm: () => void;
  label?: string;
  disabled?: boolean;
}) {
  const [armed, setArmed] = useState(false);
  if (armed) {
    return (
      <span className="inline-flex items-center gap-0.5">
        <button
          type="button"
          disabled={disabled}
          onClick={() => {
            onConfirm();
            setArmed(false);
          }}
          className="h-7 px-2.5 rounded-full text-[12px] font-medium text-negative hover:bg-negative/10"
        >
          确认删除
        </button>
        <button
          type="button"
          aria-label="取消"
          onClick={() => setArmed(false)}
          className="grid place-items-center h-7 w-7 rounded-full text-faint hover:bg-surface-2"
        >
          <X size={14} />
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={() => setArmed(true)}
      className="grid place-items-center h-7 w-7 rounded-full text-faint hover:text-negative hover:bg-negative/10 transition-colors"
    >
      <Trash2 size={14} />
    </button>
  );
}

export function MiniTags({ values }: { values: string[] }) {
  if (!values.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((t) => (
        <span key={t} className="inline-flex items-center h-6 px-2 rounded-md bg-surface-2 text-[11.5px] text-muted">
          {t}
        </span>
      ))}
    </div>
  );
}

export function ItemCard({ children }: { children: ReactNode }) {
  return <article className="rounded-2xl border border-line bg-surface p-3.5 flex flex-col gap-2">{children}</article>;
}
