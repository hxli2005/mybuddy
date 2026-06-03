import { AlertCircle, Check, CircleDashed, Loader2, X } from "lucide-react";
import { useState, type CSSProperties, type ReactNode } from "react";

export function PageHeader({
  title,
  eyebrow,
  description,
  actions,
}: {
  title: string;
  eyebrow?: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {eyebrow ? <p>{eyebrow}</p> : null}
        <h2>{title}</h2>
        {description ? <span>{description}</span> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Panel({
  title,
  description,
  actions,
  children,
  className = "",
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title || description || actions ? (
        <header className="panel-header">
          <div>
            {title ? <h3>{title}</h3> : null}
            {description ? <p>{description}</p> : null}
          </div>
          {actions ? <div className="inline-actions">{actions}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function EmptyState({
  title = "没有内容",
  text,
  action,
}: {
  title?: string;
  text: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <CircleDashed size={18} />
      <div className="empty-state-copy">
        <strong>{title}</strong>
        <p>{text}</p>
      </div>
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="error-state" role="alert">
      <AlertCircle size={18} />
      <div>
        <strong>请求失败</strong>
        <p>{message}</p>
      </div>
    </div>
  );
}

export function LoadingState({ label = "正在读取" }: { label?: string }) {
  return (
    <section className="view">
      <div className="loading-state" aria-live="polite">
        <Loader2 size={18} />
        <span>{label}</span>
      </div>
      <div className="skeleton-grid" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </section>
  );
}

export function Metric({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

export function SegmentedControl<T extends string>({
  value,
  items,
  onChange,
  label,
}: {
  value: T;
  items: Array<{ value: T; label: string; count?: number }>;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div aria-label={label} className="segmented" role="tablist">
      {items.map((item) => {
        const active = value === item.value;
        return (
          <button
            aria-selected={active}
            className={active ? "active" : ""}
            key={item.value}
            onClick={() => onChange(item.value)}
            role="tab"
            type="button"
          >
            <span>{item.label}</span>
            {typeof item.count === "number" ? <small>{item.count}</small> : null}
          </button>
        );
      })}
    </div>
  );
}

export function Tags({ values }: { values: Array<string | number> }) {
  if (!values.length) return null;
  return (
    <div className="tag-row">
      {values.map((value) => (
        <span className="tag" key={String(value)}>
          {String(value)}
        </span>
      ))}
    </div>
  );
}

export function ConfidenceMeter({ value }: { value: number }) {
  const percent = Math.max(0, Math.min(100, Math.round(value * 100)));
  return (
    <span className="confidence-meter" style={{ "--confidence": `${percent}%` } as CSSProperties}>
      <span />
      <b>{percent}%</b>
    </span>
  );
}

export function ConfirmAction({
  label,
  confirmLabel = "确认",
  title,
  disabled,
  tone = "danger",
  onConfirm,
  children,
}: {
  label: string;
  confirmLabel?: string;
  title?: string;
  disabled?: boolean;
  tone?: "danger" | "neutral";
  onConfirm: () => void;
  children: ReactNode;
}) {
  const [confirming, setConfirming] = useState(false);
  const activeLabel = confirming ? confirmLabel : label;

  return (
    <button
      aria-label={activeLabel}
      className={`icon-button ${tone === "danger" ? "danger-button" : ""}`}
      data-state={confirming ? "confirm" : undefined}
      disabled={disabled}
      onBlur={() => setConfirming(false)}
      onClick={() => {
        if (confirming) {
          onConfirm();
          setConfirming(false);
          return;
        }
        setConfirming(true);
      }}
      title={title || activeLabel}
      type="button"
    >
      {confirming ? <Check size={15} /> : children}
    </button>
  );
}

export function CancelEditButton({ onClick, label = "取消编辑" }: { onClick: () => void; label?: string }) {
  return (
    <button aria-label={label} className="icon-button" onClick={onClick} title={label} type="button">
      <X size={15} />
    </button>
  );
}
