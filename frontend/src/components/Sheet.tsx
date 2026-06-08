import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "../lib/cn";
import { IconButton } from "./ui";

type SheetProps = {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  /** 顶部右侧的额外操作 */
  actions?: ReactNode;
  side?: "right" | "left";
};

export function Sheet({ open, onClose, title, subtitle, children, actions, side = "right" }: SheetProps) {
  const [mounted, setMounted] = useState(open);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    if (open) {
      setMounted(true);
      const r = requestAnimationFrame(() => setShown(true));
      return () => cancelAnimationFrame(r);
    }
    setShown(false);
    const t = setTimeout(() => setMounted(false), 300);
    return () => clearTimeout(t);
  }, [open]);

  useEffect(() => {
    if (!mounted) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [mounted, onClose]);

  if (!mounted) return null;

  const hidden = side === "right" ? "translate-x-full" : "-translate-x-full";

  return createPortal(
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true">
      <div
        onClick={onClose}
        className={cn(
          "absolute inset-0 bg-black/35 backdrop-blur-[2px] transition-opacity duration-300",
          shown ? "opacity-100" : "opacity-0",
        )}
      />
      <div
        className={cn(
          "absolute top-0 h-full w-full sm:w-[min(444px,100vw)] flex flex-col glass shadow-float",
          side === "right" ? "right-0 border-l" : "left-0 border-r",
          "border-line transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]",
          shown ? "translate-x-0" : hidden,
        )}
      >
        <header className="flex items-center gap-3 px-5 h-16 shrink-0 border-b border-line">
          <div className="min-w-0 flex-1">
            {title ? <h2 className="font-semibold text-ink truncate">{title}</h2> : null}
            {subtitle ? <p className="text-[12.5px] text-muted truncate">{subtitle}</p> : null}
          </div>
          {actions}
          <IconButton icon={X} label="关闭" onClick={onClose} />
        </header>
        <div className="flex-1 overflow-y-auto overscroll-contain">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
