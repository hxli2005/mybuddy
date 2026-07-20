import { ArrowUp, Heart, Link2, Mic, Shield, Sparkles, ThumbsDown, ThumbsUp } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FormEvent,
  KeyboardEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { fetchMessages, sendChat, sendFeedback, transcribeAudio } from "../lib/api";
import { loadGuestMessages, saveGuestMessages } from "../lib/guestStorage";
import { useAuth } from "../lib/auth";
import { Chip, EmptyState, TypingDots } from "../components/ui";
import { ChatCbtPrompt } from "../components/ChatCbtPrompt";
import { ChatCrisisBanner } from "../components/ChatCrisisBanner";
import { GuestBanner } from "../components/GuestBanner";
import { cn } from "../lib/cn";
import { useMediaRecorder } from "../lib/useMediaRecorder";
import { queryKeys } from "../lib/queryKeys";
import type {
  ChatLogMessage,
  ChatResponse,
  Emotion,
  EmotionalSupport,
  PendingMessage,
  SearchSource,
} from "../types/api";

type CbtPromptData = {
  technique?: string;
  title?: string;
  description?: string;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  sources?: SearchSource[];
  support?: EmotionalSupport | null;
  emotion?: Emotion | null;
  turnId?: string | null;
  proactive?: string | null;
  cbtPrompt?: CbtPromptData | null;
  crisisAlert?: boolean;
};

type ChatViewProps = {
  onChatResult: (response: ChatResponse) => void;
  onOpenCrisis: () => void;
};

const quickPrompts = ["陪我说会儿话", "帮我理一理今天", "提醒我晚点复盘", "把这件事记下来"];
const defaultPendingStatus = "正在想怎么回你…";
const explicitSearchPattern =
  /(查一下|搜一下|搜索|上网|新闻|热搜|热点|链接|出处|来源|引用|官网|官方|current|latest|news|search|source|link)/i;
const timeSensitivePattern =
  /(最新|最近|现在|目前|当前|今天|昨天|刚刚|实时|今年).*(政策|法规|价格|股价|汇率|版本|发布|比赛|赛程|榜单|公司|产品|模型|论文|事件|事故|争议|口碑|趋势)/i;

export function ChatView({ onChatResult, onOpenCrisis }: ChatViewProps) {
  const queryClient = useQueryClient();
  const { isLoggedIn, loading: authLoading } = useAuth();
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pendingStatus, setPendingStatus] = useState(defaultPendingStatus);
  const [feedbackDone, setFeedbackDone] = useState<Record<string, string>>({});
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const { recording, supported: voiceSupported, start: startVoice, stop: stopVoice } = useMediaRecorder();

  async function onMicClick() {
    if (recording) {
      const blob = await stopVoice();
      try {
        const text = await transcribeAudio(blob);
        if (text.trim()) {
          setMessage((prev) => (prev + " " + text.trim()).trim());
        }
      } catch (e) {
        setMessages((current) => [
          ...current,
          { id: makeId("system"), role: "system", text: `语音识别失败：${errText(e)}` },
        ]);
      }
    } else {
      startVoice();
    }
  }

  const showCrisisBanner = messages.some((m) => m.crisisAlert);

  const historyQuery = useQuery({ queryKey: queryKeys.messages, queryFn: () => fetchMessages(100) });

  // 访客模式:挂载时从 localStorage 恢复对话(刷新不丢)
  // authLoading 守卫:等 fetchCurrentUser 完成后再判断是否加载访客消息,
  // 避免登录用户刷新时短暂 isLoggedIn=false 期间误加载 localStorage 旧对话。
  useEffect(() => {
    if (isLoggedIn || authLoading) return;
    const guest = loadGuestMessages();
    if (!guest.length) return;
    setMessages((current) =>
      current.length
        ? current
        : guest.map((m, i) => ({ id: `guest-${i}`, role: m.role, text: m.content })),
    );
  }, [isLoggedIn, authLoading]);

  // 访客模式:对话变化时写回 localStorage(上限 50 条)
  useEffect(() => {
    if (isLoggedIn) return;
    const compact = messages
      .filter((m): m is ChatMessage & { role: "user" | "assistant" } =>
        m.role === "user" || m.role === "assistant",
      )
      .map((m) => ({ role: m.role, content: m.text }));
    if (compact.length) saveGuestMessages(compact);
  }, [messages, isLoggedIn]);

  useEffect(() => {
    if (!historyQuery.data?.messages.length) return;
    setMessages((current) => (current.length ? current : historyQuery.data.messages.flatMap(historyToChat)));
  }, [historyQuery.data]);

  // 设置里"清除所有数据"成功后,立即清空聊天界面
  useEffect(() => {
    function onCleared() {
      setMessages([]);
      setFeedbackDone({});
    }
    window.addEventListener("mybuddy:data-cleared", onCleared);
    return () => window.removeEventListener("mybuddy:data-cleared", onCleared);
  }, []);

  const chatMutation = useMutation({
    mutationFn: sendChat,
    onSuccess: (data) => {
      const proactive = (data.pending_messages || []).map(pendingToChat);
      const cbtPrompt = data.cbt_prompt as CbtPromptData | undefined;
      const crisisAlert = Boolean(data.crisis_alert);
      setMessages((current) => [
        ...current,
        ...proactive,
        {
          id: makeId("assistant"),
          role: "assistant",
          text: data.text || "（这次没有文字回应）",
          sources: data.search_sources || [],
          support: hasSupport(data.emotional_support) ? data.emotional_support : null,
          emotion: data.emotion || null,
          turnId: data.turn_id || null,
          cbtPrompt: cbtPrompt || null,
          crisisAlert,
        },
      ]);
      queryClient.invalidateQueries({ queryKey: queryKeys.messages });
      onChatResult(data);
    },
    onError: (error) => {
      setMessages((current) => [
        ...current,
        { id: makeId("system"), role: "system", text: errText(error) },
      ]);
    },
    onSettled: () => setPendingStatus(defaultPendingStatus),
  });

  const feedbackMutation = useMutation({
    mutationFn: ({ label, turnId }: { label: string; turnId: string }) => sendFeedback(label, turnId),
    onSuccess: (_data, vars) => setFeedbackDone((m) => ({ ...m, [vars.turnId]: vars.label })),
    onError: (error) =>
      setMessages((current) => [
        ...current,
        { id: makeId("system"), role: "system", text: `反馈没记上：${errText(error)}` },
      ]),
  });

  // 自动滚到底
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, chatMutation.isPending]);

  // textarea 自增高
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [message]);

  function submitMessage(next = message) {
    const clean = next.trim();
    if (!clean || chatMutation.isPending) return;
    setMessage("");
    setPendingStatus(pendingStatusFor(clean));
    setMessages((current) => [...current, { id: makeId("user"), role: "user", text: clean }]);
    chatMutation.mutate(clean);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submitMessage();
  }

  function onComposerKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitMessage();
    }
  }

  function onFeedback(turnId: string, label: string) {
    if (feedbackDone[turnId] || feedbackMutation.isPending) return;
    feedbackMutation.mutate({ label, turnId });
  }

  const empty = messages.length === 0;

  return (
    <div className="h-full flex flex-col">
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden">
        <div
          className={cn(
            "mx-auto w-full max-w-2xl px-4 sm:px-5 py-6 flex flex-col gap-3.5",
            empty && "min-h-full justify-center",
          )}
        >
          {showCrisisBanner ? (
            <ChatCrisisBanner onOpenCrisis={onOpenCrisis} />
          ) : null}

          {empty ? (
            <EmptyState
              icon={Sparkles}
              title={historyQuery.isLoading ? "正在翻看我们之前聊的…" : "嘿，我在呢"}
              text={
                historyQuery.isLoading
                  ? "从本地记录里恢复对话。"
                  : "随便说一句、一个念头，或者从下面挑一个开头。"
              }
              action={
                <div className="flex flex-wrap sm:justify-center gap-2 pt-1 px-2 -mx-2">
                  {quickPrompts.map((p) => (
                    <button key={p} type="button" onClick={() => submitMessage(p)} className="shrink-0">
                      <Chip className="cursor-pointer transition-colors hover:border-line-strong hover:bg-surface">
                        {p}
                      </Chip>
                    </button>
                  ))}
                </div>
              }
            />
          ) : (
            messages.map((m) => (
              <MessageRow
                key={m.id}
                message={m}
                feedbackLabel={m.turnId ? feedbackDone[m.turnId] : undefined}
                onFeedback={onFeedback}
              />
            ))
          )}

          {chatMutation.isPending ? (
            <div className="flex items-end gap-2.5 self-start animate-fade-up">
              <div className="rounded-2xl rounded-bl-md bg-surface border border-line shadow-soft px-4 py-3 flex items-center gap-2.5">
                <TypingDots />
                <span className="text-[13px] text-muted">{pendingStatus}</span>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <div className="shrink-0 glass border-t border-line">
        {!isLoggedIn ? <GuestBanner /> : null}
        <form onSubmit={onSubmit} className="composer-area mx-auto w-full max-w-2xl px-4 sm:px-5 py-3">
          <div className="flex items-end gap-2 rounded-3xl bg-surface border border-line shadow-card focus-within:border-accent/40 transition-colors p-1.5 pl-4">
            {voiceSupported ? (
              <button
                type="button"
                aria-label={recording ? "停止录音" : "语音输入"}
                onClick={onMicClick}
                className={cn(
                  "grid place-items-center h-10 w-10 rounded-full shrink-0 transition-all duration-200 active:scale-95",
                  recording
                    ? "bg-red-100 text-red-500 animate-pulse ring-2 ring-red-400"
                    : "bg-surface-2 text-muted hover:text-ink",
                )}
              >
                <Mic size={19} strokeWidth={2.2} />
              </button>
            ) : null}
            <textarea
              ref={textareaRef}
              aria-label="消息"
              rows={1}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={onComposerKeyDown}
              placeholder="想和我说点什么…"
              className="flex-1 resize-none bg-transparent py-2.5 text-[15px] leading-relaxed text-ink placeholder:text-faint focus:outline-none max-h-[200px]"
            />
            <button
              type="submit"
              aria-label="发送"
              disabled={!message.trim() || chatMutation.isPending}
              className={cn(
                "grid place-items-center h-10 w-10 rounded-full shrink-0 transition-all duration-200 active:scale-95",
                message.trim() && !chatMutation.isPending
                  ? "bg-accent text-accent-fg shadow-soft hover:bg-accent-strong"
                  : "bg-surface-2 text-faint",
              )}
            >
              <ArrowUp size={19} strokeWidth={2.2} />
            </button>
          </div>
          <p className="pointer-coarse:hidden text-[11.5px] text-faint text-center mt-2">Enter 发送 · Shift+Enter 换行</p>
        </form>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- message */

function MessageRow({
  message,
  feedbackLabel,
  onFeedback,
}: {
  message: ChatMessage;
  feedbackLabel?: string;
  onFeedback: (turnId: string, label: string) => void;
}) {
  if (message.role === "system") {
    return (
      <div className="self-center my-1 animate-fade-in">
        <span className="text-[12px] text-faint">{message.text}</span>
      </div>
    );
  }

  if (message.proactive) {
    return (
      <div className="self-start max-w-[88%] animate-fade-up">
        <div className="flex items-center gap-1.5 text-[11.5px] text-accent mb-1 pl-1">
          <Heart size={12} strokeWidth={2.2} />
          小布主动找你
        </div>
        <div className="rounded-2xl rounded-bl-md bg-accent-soft border border-transparent px-4 py-2.5 text-[14.5px] leading-relaxed text-ink whitespace-pre-wrap">
          {message.text}
        </div>
      </div>
    );
  }

  const isUser = message.role === "user";

  return (
    <div className={cn("group flex flex-col animate-fade-up", isUser ? "items-end" : "items-start")}>
      <div
        className={cn(
          "max-w-[85%] px-4 py-2.5 text-[14.5px] leading-relaxed whitespace-pre-wrap",
          isUser
            ? "bg-accent text-accent-fg rounded-2xl rounded-br-md shadow-soft break-words"
            : "bg-surface text-ink border border-line rounded-2xl rounded-bl-md shadow-soft break-words",
        )}
      >
        {message.text}
      </div>

      {!isUser && message.sources && message.sources.length > 0 ? (
        <SourceList sources={message.sources} />
      ) : null}

      {!isUser && message.cbtPrompt ? <ChatCbtPrompt data={message.cbtPrompt} /> : null}

      {!isUser && message.support ? <SupportReveal support={message.support} /> : null}

      {!isUser && message.turnId ? (
        <div
          className={cn(
            "flex items-center gap-1 mt-1.5 pl-1 transition-opacity",
            feedbackLabel ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-within:opacity-100",
          )}
        >
          {feedbackLabel ? (
            <span className="text-[11.5px] text-muted">
              {feedbackLabel === "good" ? "谢谢，记下了 ✓" : "好的，我会调整 ✓"}
            </span>
          ) : (
            <>
              <FeedbackButton icon={ThumbsUp} label="这条很好" onClick={() => onFeedback(message.turnId!, "good")} />
              <FeedbackButton icon={ThumbsDown} label="这条不太对" onClick={() => onFeedback(message.turnId!, "bad")} />
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

function FeedbackButton({ icon: Icon, label, onClick }: { icon: typeof ThumbsUp; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="grid place-items-center h-7 w-7 rounded-full text-faint hover:text-ink hover:bg-surface-2 transition-colors"
    >
      <Icon size={14} strokeWidth={2} />
    </button>
  );
}

function SourceList({ sources }: { sources: SearchSource[] }) {
  return (
    <details className="mt-1.5 max-w-[85%] w-full group/src">
      <summary className="inline-flex items-center gap-1.5 cursor-pointer text-[12px] text-muted hover:text-ink list-none select-none">
        <Link2 size={13} strokeWidth={2} />
        资料来源 · {sources.length}
      </summary>
      <ol className="mt-1.5 flex flex-col gap-1.5">
        {sources.map((s, i) => (
          <li key={`${s.url}-${i}`} className="rounded-xl bg-surface-2 border border-line px-3 py-2">
            {s.url ? (
              <a href={s.url} target="_blank" rel="noreferrer" className="text-[13px] font-medium text-accent hover:underline break-words">
                {s.title || s.url}
              </a>
            ) : (
              <span className="text-[13px] font-medium text-ink">{s.title}</span>
            )}
            {s.snippet ? <p className="text-[12px] text-muted mt-0.5 line-clamp-2">{s.snippet}</p> : null}
          </li>
        ))}
      </ol>
    </details>
  );
}

function SupportReveal({ support }: { support: EmotionalSupport }) {
  const rows: Array<[string, string | undefined]> = [
    ["我听到的", support.mirror],
    ["你也许需要", support.need],
    ["可以试试", support.small_action],
    ["要紧的话", support.safety_note],
  ];
  const visible = rows.filter(([, v]) => v && v.trim());
  if (!visible.length) return null;
  return (
    <details className="mt-1.5 max-w-[85%] w-full">
      <summary className="inline-flex items-center gap-1.5 cursor-pointer text-[12px] text-muted hover:text-ink list-none select-none">
        <Heart size={13} strokeWidth={2} />
        小布的心里话
      </summary>
      <div className="mt-1.5 rounded-xl bg-surface-2 border border-line px-3.5 py-3 flex flex-col gap-2">
        {visible.map(([k, v]) => (
          <div key={k} className="flex flex-col gap-0.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-faint">{k}</span>
            <span className="text-[13px] text-ink-soft leading-relaxed">{v}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

/* ---------------------------------------------------------------- helpers */

function makeId(role: string) {
  return `${role}-${performance.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

function hasSupport(s: EmotionalSupport | null | undefined): s is EmotionalSupport {
  return Boolean(s && (s.mirror || s.need || s.small_action || s.safety_note));
}

function errText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function pendingStatusFor(text: string): string {
  if (explicitSearchPattern.test(text) || timeSensitivePattern.test(text)) return "正在帮你看看资料…";
  return defaultPendingStatus;
}

function pendingToChat(item: PendingMessage): ChatMessage {
  return {
    id: makeId("proactive"),
    role: "assistant",
    text: item.content,
    proactive: item.source || "nudge",
  };
}

function historyToChat(item: ChatLogMessage): ChatMessage[] {
  if (item.role !== "user" && item.role !== "assistant") return [];
  if (!item.content.trim()) return [];
  return [
    {
      id: `history-${item.id}`,
      role: item.role,
      text: item.content,
      sources: normalizeSources(item.meta?.search_sources),
    },
  ];
}

function normalizeSources(value: unknown): SearchSource[] {
  if (!Array.isArray(value)) return [];
  const out: SearchSource[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const raw = item as Record<string, unknown>;
    const title = String(raw.title || "").trim();
    const url = String(raw.url || "").trim();
    if (!url && !title) continue;
    out.push({ title: title || url, url, snippet: String(raw.snippet || "").trim(), date: String(raw.date || "").trim() });
  }
  return out;
}
