import { ArrowRight, Bell, BookOpen, Brain, MessageSquare, NotebookText, Settings, Sparkles } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { fetchMemory, fetchNotes, fetchProfile, fetchReminders, fetchSkills, fetchStatus } from "../api/client";
import { EmptyState, ErrorState, LoadingState, Metric, PageHeader, Panel } from "../components/Primitives";
import { queryKeys, type ViewId } from "../state/observability";

type OverviewViewProps = {
  onNavigate: (view: ViewId) => void;
};

export function OverviewView({ onNavigate }: OverviewViewProps) {
  const status = useQuery({ queryKey: queryKeys.status, queryFn: fetchStatus });
  const profile = useQuery({ queryKey: queryKeys.profile, queryFn: fetchProfile });
  const memory = useQuery({ queryKey: queryKeys.memory, queryFn: fetchMemory });
  const reminders = useQuery({ queryKey: queryKeys.reminders, queryFn: fetchReminders });
  const skills = useQuery({ queryKey: queryKeys.skills, queryFn: fetchSkills });
  const notes = useQuery({ queryKey: queryKeys.notes, queryFn: fetchNotes });
  const loading = [status, profile, memory, reminders, skills, notes].some((query) => query.isLoading);
  const error = [status, profile, memory, reminders, skills, notes].find((query) => query.error)?.error;

  if (loading) return <LoadingState label="正在汇总工作区" />;
  if (error) return <ErrorState error={error} />;

  const activeSkills = skills.data?.skills.filter((item) => !item.archived).length || 0;
  const pendingReminders = reminders.data?.reminders.filter((item) => item.status === "pending").length || 0;
  const pendingMessages = reminders.data?.pending_messages.length || 0;
  const claims = profile.data?.claims || [];

  return (
    <section className="view overview-view">
      <PageHeader
        actions={
          <button className="text-button" onClick={() => onNavigate("chat")} type="button">
            <MessageSquare size={16} />
            <span>继续对话</span>
          </button>
        }
        description="总览只回答一个问题：下一步最应该处理哪里。"
        eyebrow={status.data?.model || "model pending"}
        title="工作台"
      />

      <div className="metric-grid">
        <Metric hint="可编辑长期记忆" label="记忆档案" value={memory.data?.archive.length || 0} />
        <Metric hint="稳定画像字段" label="画像字段" value={Object.keys(profile.data?.fields || {}).length} />
        <Metric hint="动态推断命题" label="画像命题" value={claims.length} />
        <Metric hint={`${pendingMessages} 条待播`} label="待处理提醒" value={pendingReminders} />
        <Metric hint="未归档技能" label="活跃 Skills" value={activeSkills} />
        <Metric hint="可写入上下文" label="笔记" value={notes.data?.notes.length || 0} />
      </div>

      <div className="action-grid">
        <ActionTile
          icon={<MessageSquare size={18} />}
          label="开始一轮对话"
          onClick={() => onNavigate("chat")}
          text="把输入放进会话，再观察情绪和工具轨迹。"
        />
        <ActionTile
          icon={<BookOpen size={18} />}
          label="校正记忆"
          onClick={() => onNavigate("memory")}
          text="编辑长期记忆，避免旧上下文继续污染后续回复。"
        />
        <ActionTile
          icon={<Bell size={18} />}
          label="处理提醒"
          onClick={() => onNavigate("reminders")}
          text="查看定时提醒和等待播报的消息。"
        />
        <ActionTile
          icon={<Settings size={18} />}
          label="调整人格"
          onClick={() => onNavigate("persona")}
          text="修改称呼、边界和回应习惯。"
        />
      </div>

      <div className="dashboard-grid">
        <Panel title="最近记忆" description="最多显示 5 条，完整管理在记忆工作区。">
          {memory.data?.archive.length ? (
            <div className="table-list">
              {memory.data.archive.slice(0, 5).map((item) => (
                <article className="list-card compact-card" key={item.id}>
                  <strong>{String(item.metadata?.type || "memory")}</strong>
                  <p>{item.content}</p>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无记忆" text="完成对话或写入笔记后，这里会出现可复用上下文。" />
          )}
        </Panel>

        <Panel title="待播消息" description="后台调度准备推送给会话的内容。">
          {reminders.data?.pending_messages.length ? (
            <div className="table-list">
              {reminders.data.pending_messages.slice(0, 5).map((item, index) => (
                <article className="list-card compact-card" key={`${item.source}-${index}`}>
                  <strong>{item.source}</strong>
                  <p>{item.content}</p>
                  <span>{item.scheduled_at || "未指定时间"}</span>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="没有待播消息" text="当前调度队列为空。" />
          )}
        </Panel>

        <Panel title="系统材料" description="上下文质量取决于这些资料是否干净。">
          <div className="material-links">
            <button onClick={() => onNavigate("profile")} type="button">
              <Brain size={17} />
              <span>画像命题</span>
              <strong>{claims.length}</strong>
            </button>
            <button onClick={() => onNavigate("notes")} type="button">
              <NotebookText size={17} />
              <span>笔记</span>
              <strong>{notes.data?.notes.length || 0}</strong>
            </button>
            <button onClick={() => onNavigate("skills")} type="button">
              <Sparkles size={17} />
              <span>Skills</span>
              <strong>{activeSkills}</strong>
            </button>
          </div>
        </Panel>
      </div>
    </section>
  );
}

function ActionTile({
  icon,
  label,
  text,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  text: string;
  onClick: () => void;
}) {
  return (
    <button className="action-tile" onClick={onClick} type="button">
      <span>{icon}</span>
      <strong>{label}</strong>
      <p>{text}</p>
      <ArrowRight size={16} />
    </button>
  );
}
