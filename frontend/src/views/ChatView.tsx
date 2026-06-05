import { CornerDownLeft, Send, ThumbsDown, ThumbsUp } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import { fetchMessages, sendChat, sendFeedback } from "../api/client";
import { EmptyState, PageHeader, Panel } from "../components/Primitives";
import { queryKeys } from "../state/observability";
import type { ChatLogMessage, ChatResponse, PendingMessage, SearchSource } from "../types/api";

type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  sources?: SearchSource[];
};

type ChatViewProps = {
  onChatResult: (response: ChatResponse) => void;
};

const quickPrompts = ["帮我整理今天的状态", "提醒我晚点复盘", "把这段话记下来"];
const defaultPendingStatus = "正在组织回复。";
const explicitSearchPattern = /(查一下|搜一下|搜索|上网|新闻|热搜|热点|链接|出处|来源|引用|官网|官方|current|latest|news|search|source|link)/i;
const timeSensitivePattern =
  /(最新|最近|现在|目前|当前|今天|昨天|刚刚|实时|今年).*(政策|法规|价格|股价|汇率|版本|发布|比赛|赛程|榜单|公司|产品|模型|论文|事件|事故|争议|口碑|趋势)|(政策|法规|价格|股价|汇率|版本|发布会|比赛|赛程|榜单|CEO|总统|总理|产品|模型|论文|口碑|趋势)/i;
const interestFactPattern = /(剧情|设定|角色|卡牌|卡池|活动|机制|攻略|技能|数值|时间线|结局|主线|支线|声优|讲什么|哪张|哪个)/i;

export function ChatView({ onChatResult }: ChatViewProps) {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [lastTurnId, setLastTurnId] = useState<string | null>(null);
  const [pendingStatus, setPendingStatus] = useState(defaultPendingStatus);
  const [pendingBroadcasts, setPendingBroadcasts] = useState<PendingMessage[]>([]);
  const historyQuery = useQuery({ queryKey: queryKeys.messages, queryFn: () => fetchMessages(100) });

  useEffect(() => {
    if (!historyQuery.data?.messages.length) return;
    setMessages((current) => {
      if (current.length) return current;
      return historyQuery.data.messages.flatMap(historyMessageToChatMessage);
    });
  }, [historyQuery.data]);

  const chatMutation = useMutation({
    mutationFn: sendChat,
    onSuccess: (data) => {
      setLastTurnId(data.turn_id || null);
      setPendingBroadcasts(data.pending_messages || []);
      setMessages((current) => [
        ...current,
        ...(data.pending_messages || []).map(pendingMessageToChatMessage),
        createMessage("assistant", data.text || "没有文本响应。", data.search_sources || []),
      ]);
      queryClient.invalidateQueries({ queryKey: queryKeys.messages });
      onChatResult(data);
    },
    onError: (error) => {
      setMessages((current) => [
        ...current,
        createMessage("system", error instanceof Error ? error.message : String(error)),
      ]);
    },
    onSettled: () => {
      setPendingStatus(defaultPendingStatus);
    },
  });

  const feedbackMutation = useMutation({
    mutationFn: (label: string) => sendFeedback(label, lastTurnId || ""),
    onSuccess: (_, label) => {
      setMessages((current) => [...current, createMessage("system", `feedback: ${label}`)]);
    },
    onError: (error) => {
      setMessages((current) => [
        ...current,
        createMessage("system", error instanceof Error ? error.message : String(error)),
      ]);
    },
  });

  const messageGroups = useMemo(() => {
    return messages.reduce<Record<ChatMessage["role"], ChatMessage[]>>(
      (groups, item) => {
        groups[item.role].push(item);
        return groups;
      },
      { user: [], assistant: [], system: [] },
    );
  }, [messages]);

  const canSend = Boolean(message.trim()) && !chatMutation.isPending;

  function submitMessage(nextMessage = message) {
    const clean = nextMessage.trim();
    if (!clean || chatMutation.isPending) return;
    setMessage("");
    setPendingStatus(pendingStatusFor(clean));
    setMessages((current) => [...current, createMessage("user", clean)]);
    chatMutation.mutate(clean);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    submitMessage();
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitMessage();
    }
  }

  return (
    <section className="view chat-view">
      <PageHeader
        actions={
          <div className="inline-actions">
            <button
              aria-label="标记为好回复"
              className="icon-button"
              data-state={feedbackMutation.isPending ? "loading" : undefined}
              disabled={!lastTurnId || feedbackMutation.isPending}
              onClick={() => feedbackMutation.mutate("good")}
              title="标记好回复"
              type="button"
            >
              <ThumbsUp size={16} />
            </button>
            <button
              aria-label="标记为坏回复"
              className="icon-button"
              data-state={feedbackMutation.isPending ? "loading" : undefined}
              disabled={!lastTurnId || feedbackMutation.isPending}
              onClick={() => feedbackMutation.mutate("bad")}
              title="标记坏回复"
              type="button"
            >
              <ThumbsDown size={16} />
            </button>
          </div>
        }
        description="对话是主任务；右侧观察面板会同步显示情绪、工具和支持策略。"
        eyebrow={lastTurnId ? `本轮 ${lastTurnId}` : "local session"}
        title="慢慢聊"
      />

      <div className="conversation-layout">
        <Panel className="conversation-panel">
          <div className="message-list" aria-live="polite">
            {messages.length ? (
              messages.map((item) => <MessageBubble item={item} key={item.id} />)
            ) : (
              <EmptyState
                title={historyQuery.isLoading ? "正在读取历史" : "还没有开始"}
                text={historyQuery.isLoading ? "正在从本地日志恢复对话。" : "先写一句话、一个念头，或直接选一个起手式。"}
                action={
                  <div className="quick-prompts">
                    {quickPrompts.map((prompt) => (
                      <button key={prompt} onClick={() => submitMessage(prompt)} type="button">
                        {prompt}
                      </button>
                    ))}
                  </div>
                }
              />
            )}
            {chatMutation.isPending ? (
              <article className="message assistant pending">
                <span className="message-meta">MyBuddy</span>
                <p>{pendingStatus}</p>
              </article>
            ) : null}
          </div>
          <form className="composer" onSubmit={submit}>
            <label className="composer-field">
              <span>消息</span>
              <textarea
                aria-label="消息"
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={handleComposerKeyDown}
                placeholder="今天有一件事是…"
                rows={3}
                value={message}
              />
            </label>
            <button className="send-button" data-state={chatMutation.isPending ? "loading" : undefined} disabled={!canSend} type="submit">
              <Send size={17} />
              <span>{chatMutation.isPending ? "发送中" : "发送"}</span>
            </button>
            <span className="composer-hint">
              <CornerDownLeft size={14} />
              Enter 发送，Shift Enter 换行
            </span>
          </form>
        </Panel>

        <aside className="conversation-aside">
          <Panel title="本轮结构" description="把会话拆成可检查的来源。">
            <div className="thread-map">
              <ThreadCount label="你" value={messageGroups.user.length} />
              <ThreadCount label="MyBuddy" value={messageGroups.assistant.length} />
              <ThreadCount label="系统" value={messageGroups.system.length} />
            </div>
          </Panel>
          <Panel title="待播消息" description="来自提醒或后台调度的内容。">
            {pendingBroadcasts.length ? (
              <div className="table-list">
                {pendingBroadcasts.map((item, index) => (
                  <article className="list-card compact-card" key={`${item.source}-${index}`}>
                    <strong>{item.source}</strong>
                    <p>{item.content}</p>
                    <span>{item.scheduled_at || "待定时间"}</span>
                  </article>
                ))}
              </div>
            ) : (
              <EmptyState title="没有待播" text="当前没有等待播报的提醒或后台消息。" />
            )}
          </Panel>
        </aside>
      </div>
    </section>
  );
}

function MessageBubble({ item }: { item: ChatMessage }) {
  return (
    <article className={`message ${item.role}`}>
      <span className="message-meta">{messageLabel(item.role)}</span>
      <p>{item.text}</p>
      {item.role === "assistant" && item.sources?.length ? (
        <details className="message-sources">
          <summary>资料来源</summary>
          <ol>
            {item.sources.map((source, index) => (
              <li key={`${source.url}-${index}`}>
                {source.url ? (
                  <a href={source.url} rel="noreferrer" target="_blank">
                    {source.title || source.url}
                  </a>
                ) : (
                  <strong>{source.title}</strong>
                )}
                {source.date ? <span>{source.date}</span> : null}
                {source.snippet ? <p>{source.snippet}</p> : null}
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </article>
  );
}

function ThreadCount({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function createMessage(role: ChatMessage["role"], text: string, sources: SearchSource[] = []): ChatMessage {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    role,
    text,
    sources,
  };
}

function pendingStatusFor(text: string): string {
  if (explicitSearchPattern.test(text) || timeSensitivePattern.test(text) || interestFactPattern.test(text)) return "正在看资料。";
  return defaultPendingStatus;
}

function pendingMessageToChatMessage(item: PendingMessage): ChatMessage {
  if (item.role === "assistant") {
    return createMessage("assistant", item.content);
  }
  return createMessage("system", `${item.source}: ${item.content}`);
}

function historyMessageToChatMessage(item: ChatLogMessage): ChatMessage[] {
  if (item.role !== "user" && item.role !== "assistant") return [];
  if (!item.content.trim()) return [];
  return [
    {
      id: `history-${item.id}`,
      role: item.role,
      text: item.content,
      sources: normalizeSearchSources(item.meta?.search_sources),
    },
  ];
}

function normalizeSearchSources(value: unknown): SearchSource[] {
  if (!Array.isArray(value)) return [];
  const sources: SearchSource[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const raw = item as Record<string, unknown>;
    const title = String(raw.title || "").trim();
    const url = String(raw.url || "").trim();
    const snippet = String(raw.snippet || "").trim();
    const date = String(raw.date || "").trim();
    if (!url && !title) continue;
    sources.push({ title: title || url, url, snippet, date });
  }
  return sources;
}

function messageLabel(role: ChatMessage["role"]): string {
  if (role === "assistant") return "MyBuddy";
  if (role === "user") return "你";
  return "系统";
}
