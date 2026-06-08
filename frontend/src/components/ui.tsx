import { forwardRef } from "react";
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  ReactNode,
  TextareaHTMLAttributes,
} from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "../lib/cn";

/* ----------------------------------------------------------------- Button */

type ButtonVariant = "primary" | "secondary" | "ghost" | "soft";
type ButtonSize = "sm" | "md" | "lg";

const buttonBase =
  "inline-flex items-center justify-center gap-2 font-medium rounded-full select-none " +
  "transition-[background-color,border-color,color,transform,box-shadow] duration-200 " +
  "active:scale-[0.98] disabled:opacity-45 disabled:pointer-events-none " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

const buttonVariants: Record<ButtonVariant, string> = {
  primary: "bg-accent text-accent-fg shadow-soft hover:bg-accent-strong",
  secondary:
    "bg-surface text-ink border border-line hover:border-line-strong hover:bg-surface-2",
  soft: "bg-accent-soft text-accent hover:bg-accent-soft/70",
  ghost: "text-ink-soft hover:bg-surface-2 hover:text-ink",
};

const buttonSizes: Record<ButtonSize, string> = {
  sm: "h-9 px-4 text-[13px]",
  md: "h-11 px-5 text-sm",
  lg: "h-12 px-6 text-[15px]",
};

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", className, type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(buttonBase, buttonVariants[variant], buttonSizes[size], className)}
      {...props}
    />
  );
});

/* ------------------------------------------------------------- IconButton */

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: LucideIcon;
  label: string;
  size?: number;
  tone?: "default" | "accent";
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon: Icon, label, size = 18, tone = "default", className, type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      className={cn(
        "inline-grid place-items-center h-10 w-10 rounded-full transition-all duration-200",
        "active:scale-95 disabled:opacity-40 disabled:pointer-events-none",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        tone === "accent"
          ? "text-accent hover:bg-accent-soft"
          : "text-muted hover:text-ink hover:bg-surface-2",
        className,
      )}
      {...props}
    >
      <Icon size={size} strokeWidth={1.9} />
    </button>
  );
});

/* --------------------------------------------------------------- Surface */

type SurfaceProps = HTMLAttributes<HTMLDivElement> & {
  as?: "div" | "section" | "article";
  inset?: boolean;
};

export function Surface({ className, inset, ...props }: SurfaceProps) {
  return (
    <div
      className={cn(
        "rounded-2xl border border-line",
        inset ? "bg-surface-2" : "bg-surface shadow-card",
        className,
      )}
      {...props}
    />
  );
}

/* ------------------------------------------------------------------ Chip */

type ChipProps = HTMLAttributes<HTMLSpanElement> & {
  tone?: "neutral" | "accent" | "positive" | "negative";
};

const chipTones: Record<NonNullable<ChipProps["tone"]>, string> = {
  neutral: "bg-surface-2 text-ink-soft border-line",
  accent: "bg-accent-soft text-accent border-transparent",
  positive: "bg-positive/12 text-positive border-transparent",
  negative: "bg-negative/12 text-negative border-transparent",
};

export function Chip({ tone = "neutral", className, ...props }: ChipProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 h-7 px-3 rounded-full border text-[12.5px] font-medium whitespace-nowrap",
        chipTones[tone],
        className,
      )}
      {...props}
    />
  );
}

/* --------------------------------------------------------------- Divider */

export function Divider({ className }: { className?: string }) {
  return <hr className={cn("border-0 border-t border-line", className)} />;
}

/* ------------------------------------------------------------------ Field */

type FieldProps = {
  label?: string;
  hint?: string;
  children: ReactNode;
  className?: string;
};

export function Field({ label, hint, children, className }: FieldProps) {
  return (
    <label className={cn("flex flex-col gap-1.5", className)}>
      {label ? (
        <span className="text-[12.5px] font-medium text-muted px-1">{label}</span>
      ) : null}
      {children}
      {hint ? <span className="text-[12px] text-faint px-1">{hint}</span> : null}
    </label>
  );
}

const inputBase =
  "w-full rounded-xl bg-surface-2 border border-line text-ink placeholder:text-faint " +
  "transition-colors duration-200 focus:bg-surface focus:border-accent/40 " +
  "focus-visible:outline-none";

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(inputBase, "h-11 px-3.5 text-sm", className)} {...props} />;
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return (
      <textarea ref={ref} className={cn(inputBase, "px-3.5 py-2.5 text-sm resize-none", className)} {...props} />
    );
  },
);

/* ----------------------------------------------------------------- Avatar */

type AvatarProps = {
  size?: number;
  initial?: string;
  className?: string;
  /** 在场呼吸点的色调,通常跟随情绪 */
  presence?: "calm" | "positive" | "negative" | null;
};

export function Avatar({ size = 36, initial = "布", presence = "calm", className }: AvatarProps) {
  return (
    <span className={cn("relative inline-grid place-items-center shrink-0", className)} style={{ width: size, height: size }}>
      <span
        className="absolute inset-0 rounded-full bg-gradient-to-br from-accent to-accent-strong"
        aria-hidden
      />
      <span
        className="relative font-semibold text-accent-fg leading-none"
        style={{ fontSize: size * 0.42 }}
      >
        {initial}
      </span>
      {presence ? (
        <span
          className={cn(
            "absolute -bottom-0.5 -right-0.5 rounded-full ring-2 ring-bg animate-breathe",
            presence === "positive" && "bg-positive",
            presence === "negative" && "bg-negative",
            presence === "calm" && "bg-accent",
          )}
          style={{ width: size * 0.26, height: size * 0.26 }}
          aria-hidden
        />
      ) : null}
    </span>
  );
}

/* ---------------------------------------------------------------- Spinner */

export function Spinner({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <span
      className={cn("inline-block rounded-full border-2 border-line-strong border-t-accent animate-spin", className)}
      style={{ width: size, height: size }}
      aria-hidden
    />
  );
}

/** 拟人化的"输入中"三点 */
export function TypingDots({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1", className)} aria-label="正在输入">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 rounded-full bg-muted animate-breathe"
          style={{ animationDelay: `${i * 0.18}s`, animationDuration: "1.2s" }}
        />
      ))}
    </span>
  );
}

/* ------------------------------------------------------------- EmptyState */

type EmptyStateProps = {
  icon?: LucideIcon;
  title: string;
  text?: string;
  action?: ReactNode;
  className?: string;
};

export function EmptyState({ icon: Icon, title, text, action, className }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center text-center gap-3 py-12 px-6", className)}>
      {Icon ? (
        <span className="grid place-items-center h-14 w-14 rounded-2xl bg-surface-2 text-faint">
          <Icon size={24} strokeWidth={1.7} />
        </span>
      ) : null}
      <div className="space-y-1">
        <p className="font-medium text-ink-soft">{title}</p>
        {text ? <p className="text-[13.5px] text-muted max-w-xs text-balance">{text}</p> : null}
      </div>
      {action}
    </div>
  );
}

/* ----------------------------------------------------------- SectionLabel */

export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p className={cn("text-[11.5px] font-semibold uppercase tracking-[0.08em] text-faint px-1", className)}>
      {children}
    </p>
  );
}
