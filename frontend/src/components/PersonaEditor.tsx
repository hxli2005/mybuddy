import { RotateCcw, Save } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { ErrorState, Panel } from "./Primitives";
import type { Persona } from "../types/api";

type Draft = {
  name: string;
  relationship: string;
  style: string;
  tone: string;
  boundaries: string;
  language: string;
  address_user: string;
  response_habits: string;
};

const emptyDraft: Draft = {
  name: "",
  relationship: "",
  style: "",
  tone: "",
  boundaries: "",
  language: "中文",
  address_user: "你",
  response_habits: "",
};

export function PersonaEditor({
  persona,
  formId,
  saving = false,
  error,
  onSave,
}: {
  persona: Persona;
  formId: string;
  saving?: boolean;
  error?: unknown;
  onSave: (persona: Persona) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(persona));
  const [baseline, setBaseline] = useState<Draft>(() => toDraft(persona));

  useEffect(() => {
    const next = toDraft(persona);
    setDraft(next);
    setBaseline(next);
  }, [persona]);

  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(baseline), [baseline, draft]);
  const habits = useMemo(() => splitLines(draft.response_habits), [draft.response_habits]);

  function submit(event: FormEvent) {
    event.preventDefault();
    onSave(fromDraft(draft));
  }

  return (
    <div className="persona-layout">
      <Panel
        title="配置表单"
        description={dirty ? "有未保存改动" : "当前配置已同步"}
        actions={
          <>
            <button className="text-button" disabled={!dirty || saving} onClick={() => setDraft(baseline)} type="button">
              <RotateCcw size={16} />
              <span>还原</span>
            </button>
            <button
              className="text-button primary"
              data-state={saving ? "loading" : undefined}
              disabled={!dirty || saving}
              form={formId}
              type="submit"
            >
              <Save size={16} />
              <span>{saving ? "保存中" : "保存配置"}</span>
            </button>
          </>
        }
      >
        <form className="persona-form" id={formId} onSubmit={submit}>
          <div className="form-grid">
            <label>
              名字
              <input value={draft.name} onChange={(event) => update("name", event.target.value)} />
            </label>
            <label>
              回复语言
              <input value={draft.language} onChange={(event) => update("language", event.target.value)} />
            </label>
            <label>
              称呼用户
              <input value={draft.address_user} onChange={(event) => update("address_user", event.target.value)} />
            </label>
          </div>
          <label>
            关系定位
            <textarea rows={3} value={draft.relationship} onChange={(event) => update("relationship", event.target.value)} />
          </label>
          <label>
            整体风格
            <textarea rows={3} value={draft.style} onChange={(event) => update("style", event.target.value)} />
          </label>
          <label>
            语气细节
            <textarea rows={4} value={draft.tone} onChange={(event) => update("tone", event.target.value)} />
          </label>
          <label>
            回应习惯
            <textarea rows={6} value={draft.response_habits} onChange={(event) => update("response_habits", event.target.value)} />
          </label>
          <label>
            边界
            <textarea rows={4} value={draft.boundaries} onChange={(event) => update("boundaries", event.target.value)} />
          </label>
        </form>
        {error ? <ErrorState error={error} /> : null}
      </Panel>

      <Panel title="配置预览" description="保存前先看它会如何影响回复。">
        <div className="persona-preview">
          <div>
            <span>身份</span>
            <strong>{draft.name || "未命名"}</strong>
            <p>{draft.relationship || "还没有关系定位。"}</p>
          </div>
          <div>
            <span>语气</span>
            <p>{draft.tone || draft.style || "还没有语气说明。"}</p>
          </div>
          <div>
            <span>边界</span>
            <p>{draft.boundaries || "还没有边界说明。"}</p>
          </div>
          <div>
            <span>回应习惯</span>
            {habits.length ? (
              <ul>
                {habits.map((habit) => (
                  <li key={habit}>{habit}</li>
                ))}
              </ul>
            ) : (
              <p>还没有固定回应习惯。</p>
            )}
          </div>
        </div>
      </Panel>
    </div>
  );

  function update(key: keyof Draft, value: string) {
    setDraft((current) => ({ ...current, [key]: value }));
  }
}

function toDraft(persona: Persona): Draft {
  return {
    name: persona.name || "",
    relationship: persona.relationship || "",
    style: persona.style || "",
    tone: persona.tone || "",
    boundaries: persona.boundaries || "",
    language: persona.language || "中文",
    address_user: persona.address_user || "你",
    response_habits: (persona.response_habits || []).join("\n"),
  };
}

function fromDraft(draft: Draft): Persona {
  return {
    name: draft.name.trim(),
    relationship: draft.relationship.trim(),
    style: draft.style.trim(),
    tone: draft.tone.trim(),
    boundaries: draft.boundaries.trim(),
    language: draft.language.trim(),
    address_user: draft.address_user.trim(),
    response_habits: splitLines(draft.response_habits),
  };
}

function splitLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}
