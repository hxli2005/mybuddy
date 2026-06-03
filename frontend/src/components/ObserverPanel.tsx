import { Activity, CircleAlert, CircleCheck, Clock3, Hammer, HeartPulse, Zap } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { fetchStatus } from "../api/client";
import { queryKeys, type RuntimeSnapshot } from "../state/observability";
import type { ToolCall } from "../types/api";

type ObserverPanelProps = {
  runtime: RuntimeSnapshot;
};

export function ObserverPanel({ runtime }: ObserverPanelProps) {
  const status = useQuery({ queryKey: queryKeys.status, queryFn: fetchStatus });
  const data = status.data;
  const configured = Boolean(data?.configured);

  return (
    <div className="observer">
      <header className="observer-heading">
        <div>
          <p>运行观察</p>
          <h2>{configured ? "在线" : "待配置"}</h2>
        </div>
        <span className={configured ? "status-dot ok" : "status-dot warn"} aria-hidden="true" />
      </header>

      <section className="observer-section">
        <div className="section-title">
          <Activity size={16} />
          <h3>系统</h3>
        </div>
        <dl className="kv-grid">
          <div>
            <dt>模型</dt>
            <dd>{data?.model || "读取中"}</dd>
          </div>
          <div>
            <dt>人格</dt>
            <dd>{data?.persona?.name || "未命名"}</dd>
          </div>
          <div>
            <dt>工具</dt>
            <dd>{data?.tools?.length ?? "-"}</dd>
          </div>
          <div>
            <dt>调度</dt>
            <dd>{data?.scheduler_jobs?.length ?? "-"}</dd>
          </div>
        </dl>
      </section>

      <section className="observer-section">
        <div className="section-title">
          <HeartPulse size={16} />
          <h3>情绪识别</h3>
        </div>
        <div className={`emotion-card ${runtime.emotion?.label || "neutral"}`}>
          <strong>{runtime.emotion?.label || "neutral"}</strong>
          <span>{Number(runtime.emotion?.strength || 0).toFixed(1)}</span>
        </div>
        {runtime.emotion?.reason ? <p className="observer-copy">{runtime.emotion.reason}</p> : null}
      </section>

      <section className="observer-section">
        <div className="section-title">
          <Zap size={16} />
          <h3>支持策略</h3>
        </div>
        {runtime.support ? (
          <div className="support-card">
            <strong>
              {runtime.support.mode || "-"} · {runtime.support.need || "-"}
            </strong>
            <p>{runtime.support.mirror}</p>
            <p>{runtime.support.small_action}</p>
            {runtime.support.safety_note ? <p className="danger">{runtime.support.safety_note}</p> : null}
          </div>
        ) : (
          <p className="muted-row">发送一轮对话后显示支持策略。</p>
        )}
      </section>

      <section className="observer-section">
        <div className="section-title">
          <Hammer size={16} />
          <h3>工具轨迹</h3>
        </div>
        <ol className="tool-trace">
          {runtime.toolCalls.length ? (
            runtime.toolCalls.map((call, index) => <ToolTraceItem call={call} key={`${call.id || call.name}-${index}`} />)
          ) : (
            <li className="muted-row">暂无工具调用。</li>
          )}
        </ol>
      </section>

      <section className="observer-section compact">
        <div className="section-title">
          <Clock3 size={16} />
          <h3>本轮</h3>
        </div>
        <div className="turn-card">
          {runtime.turnId ? <CircleCheck size={16} /> : <CircleAlert size={16} />}
          <span>{runtime.turnId || "暂无 turn"}</span>
        </div>
      </section>
    </div>
  );
}

function ToolTraceItem({ call }: { call: ToolCall }) {
  return (
    <li>
      <strong>{call.name}</strong>
      {call.source ? <span>{call.source}</span> : null}
      <code>{JSON.stringify(call.arguments || {})}</code>
      {call.result ? <small>{summarizeResult(call)}</small> : null}
    </li>
  );
}

function summarizeResult(call: ToolCall): string {
  try {
    const data = JSON.parse(call.result || "{}") as Record<string, unknown>;
    if (call.name === "weather") {
      return `${data.city || ""} ${data.condition || ""} ${data.temperature_c ?? "-"}C`;
    }
    if (call.name === "set_reminder") {
      return `${data.trigger_at || ""}`;
    }
    return "done";
  } catch {
    return "done";
  }
}
