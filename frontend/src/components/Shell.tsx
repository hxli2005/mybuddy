import {
  Bell,
  BookOpen,
  Brain,
  Command,
  Gauge,
  Menu,
  MessageSquare,
  NotebookText,
  PanelRightClose,
  PanelRightOpen,
  Search,
  Settings,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import type { RuntimeSnapshot, ViewGroup, ViewId, ViewMeta } from "../state/observability";

type NavIcon = typeof MessageSquare;

const viewIcons: Record<ViewId, NavIcon> = {
  chat: MessageSquare,
  overview: Gauge,
  memory: BookOpen,
  profile: Brain,
  reminders: Bell,
  skills: Sparkles,
  notes: NotebookText,
  persona: Settings,
};

type ShellProps = {
  activeView: ViewId;
  activeMeta: ViewMeta;
  views: ViewMeta[];
  groups: ViewGroup[];
  onViewChange: (view: ViewId) => void;
  children: ReactNode;
  inspector: ReactNode;
  runtime: RuntimeSnapshot;
};

export function Shell({
  activeView,
  activeMeta,
  views,
  groups,
  onViewChange,
  children,
  inspector,
  runtime,
}: ShellProps) {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const filteredViews = useMemo(() => {
    const clean = paletteQuery.trim().toLowerCase();
    if (!clean) return views;
    return views.filter((view) => `${view.label} ${view.summary} ${view.shortcut}`.toLowerCase().includes(clean));
  }, [paletteQuery, views]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [paletteQuery]);

  useEffect(() => {
    function handleGlobalKeyDown(event: KeyboardEvent) {
      const commandKey = event.metaKey || event.ctrlKey;
      if (commandKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
        return;
      }
      if (event.key === "/" && !isTypingTarget(event.target)) {
        event.preventDefault();
        setPaletteOpen(true);
      }
    }

    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, []);

  useEffect(() => {
    if (!paletteOpen) return;
    const frame = window.requestAnimationFrame(() => searchRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [paletteOpen]);

  useEffect(() => {
    setPaletteOpen(false);
    setInspectorOpen(false);
  }, [activeView]);

  function chooseView(view: ViewId) {
    onViewChange(view);
    setPaletteQuery("");
  }

  function handlePaletteKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setPaletteOpen(false);
      return;
    }
    if (!filteredViews.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelectedIndex((current) => (current + 1) % filteredViews.length);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelectedIndex((current) => (current === 0 ? filteredViews.length - 1 : current - 1));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      chooseView(filteredViews[selectedIndex].id);
    }
  }

  return (
    <div className={inspectorOpen ? "app-shell inspector-is-open" : "app-shell"}>
      <aside className="navigation" aria-label="主导航">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            M
          </div>
          <div>
            <h1>MyBuddy</h1>
            <p>本地陪伴系统</p>
          </div>
        </div>

        <button className="command-entry" onClick={() => setPaletteOpen(true)} type="button">
          <Search size={16} />
          <span>跳转视图</span>
          <kbd>⌘K</kbd>
        </button>

        <nav className="nav-map">
          {groups.map((group) => {
            const groupViews = views.filter((view) => view.group === group.id);
            return (
              <section className="nav-group" key={group.id}>
                <header>
                  <span>{group.label}</span>
                  <small>{group.summary}</small>
                </header>
                <div className="nav-list">
                  {groupViews.map((item) => {
                    const Icon = viewIcons[item.id];
                    const active = activeView === item.id;
                    return (
                      <button
                        aria-current={active ? "page" : undefined}
                        className={active ? "nav-item active" : "nav-item"}
                        key={item.id}
                        onClick={() => chooseView(item.id)}
                        type="button"
                      >
                        <Icon size={17} />
                        <span>{item.label}</span>
                        <kbd>{item.shortcut}</kbd>
                      </button>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </nav>

        <div className="shell-status">
          <span>{runtime.turnId ? "已接入本轮" : "等待会话"}</span>
          <small>{runtime.toolCalls.length} tool calls</small>
        </div>
      </aside>

      <main className="workspace">
        <header className="workspace-bar">
          <button className="mobile-menu-button" onClick={() => setPaletteOpen(true)} type="button">
            <Menu size={17} />
            <span>菜单</span>
          </button>
          <div>
            <p>{activeMeta.summary}</p>
            <strong>{activeMeta.label}</strong>
          </div>
          <button
            aria-pressed={inspectorOpen}
            className="inspector-toggle"
            onClick={() => setInspectorOpen((current) => !current)}
            type="button"
          >
            {inspectorOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
            <span>观察</span>
          </button>
        </header>
        {children}
      </main>

      <aside className="inspector" aria-label="运行观察">
        {inspector}
      </aside>

      {paletteOpen ? (
        <div className="palette-layer" role="presentation" onMouseDown={() => setPaletteOpen(false)}>
          <section
            aria-label="命令面板"
            aria-modal="true"
            className="command-palette"
            onMouseDown={(event) => event.stopPropagation()}
            role="dialog"
          >
            <header>
              <Command size={17} />
              <span>打开工作区</span>
              <button aria-label="关闭命令面板" className="icon-button" onClick={() => setPaletteOpen(false)} type="button">
                <X size={15} />
              </button>
            </header>
            <label className="palette-search">
              <Search size={16} />
              <input
                aria-label="搜索视图"
                onChange={(event) => setPaletteQuery(event.target.value)}
                onKeyDown={handlePaletteKeyDown}
                ref={searchRef}
                value={paletteQuery}
              />
            </label>
            <div className="palette-results" role="listbox">
              {filteredViews.length ? (
                filteredViews.map((view, index) => {
                  const Icon = viewIcons[view.id];
                  const selected = index === selectedIndex;
                  return (
                    <button
                      aria-selected={selected}
                      className={selected ? "palette-item selected" : "palette-item"}
                      key={view.id}
                      onMouseEnter={() => setSelectedIndex(index)}
                      onClick={() => chooseView(view.id)}
                      role="option"
                      type="button"
                    >
                      <Icon size={17} />
                      <span>
                        <strong>{view.label}</strong>
                        <small>{view.summary}</small>
                      </span>
                      <kbd>{view.shortcut}</kbd>
                    </button>
                  );
                })
              ) : (
                <p className="palette-empty">没有匹配的视图。</p>
              )}
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

function isTypingTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable;
}
