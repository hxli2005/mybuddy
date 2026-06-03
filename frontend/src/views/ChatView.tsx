import { CornerDownLeft, Send, ThumbsDown, ThumbsUp } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { FormEvent, KeyboardEvent, useMemo, useState } from "react";
import { sendChat, sendFeedback } from "../api/client";
import { EmptyState, PageHeader, Panel } from "../components/Primitives";
import type { ChatResponse, PendingMessage } from "../types/api";

type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
};

type ChatViewProps = {
  onChatResult: (response: ChatResponse) => void;
};

const quickPrompts = ["帮我整理今天的状态", "提醒我晚点复盘", "把这段话记下来"];

export function ChatView({ onChatResult }: ChatViewProps) {
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [lastTurnId, setLastTurnId] = useState<string | null>(null);
  const [pendingBroadcasts, setPendingBroadcasts] = useState<PendingMessage[]>([]);

  const chatMutation = useMutation({
    mutationFn: sendChat,
    onSuccess: (data) => {
      setLastTurnId(data.turn_id || null);
      setPendingBroadcasts(data.pending_messages || []);
      setMessages((current) => [
        ...current,
        createMessage("assistant", data.text || "没有文本响应。"),
        ...(data.pending_messages || []).map((item) => createMessage("system", `${item.source}: ${item.content}`)),
      ]);
      onChatResult(data);
    },
    onError: (error) => {
      setMessages((current) => [
        ...current,
        createMessage("system", error instanceof Error ? error.message : String(error)),
      ]);
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
                title="还没有开始"
                text="先写一句话、一个念头，或直接选一个起手式。"
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
                <p>正在组织回复。</p>
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

function createMessage(role: ChatMessage["role"], text: string): ChatMessage {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    role,
    text,
  };
}

function messageLabel(role: ChatMessage["role"]): string {
  if (role === "assistant") return "MyBuddy";
  if (role === "user") return "你";
  return "系统";
}
