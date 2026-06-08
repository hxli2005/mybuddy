import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save } from "lucide-react";
import { fetchPersona, savePersona } from "../../lib/api";
import { Button, Field, Input, Textarea } from "../../components/ui";
import { queryKeys } from "../../lib/queryKeys";
import { SectionState } from "./common";
import type { Persona } from "../../types/api";

type Draft = Record<string, string>;

export function PersonaSection() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.persona, queryFn: fetchPersona });
  const base = query.data?.persona;
  const [draft, setDraft] = useState<Draft>({});
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!base) return;
    setDraft({
      name: base.name || "",
      address_user: base.address_user || "",
      relationship: base.relationship || "",
      tone: base.tone || "",
      style: base.style || "",
      boundaries: base.boundaries || "",
      response_habits: (base.response_habits || []).join("\n"),
    });
    setDirty(false);
  }, [base]);

  const mutation = useMutation({
    mutationFn: (p: Persona) => savePersona(p),
    onSuccess: () => {
      setDirty(false);
      qc.invalidateQueries({ queryKey: queryKeys.persona });
      qc.invalidateQueries({ queryKey: queryKeys.status });
    },
  });

  function set(key: string, value: string) {
    setDraft((d) => ({ ...d, [key]: value }));
    setDirty(true);
  }

  function save() {
    if (!base) return;
    mutation.mutate({
      ...base,
      name: (draft.name || "").trim(),
      address_user: (draft.address_user || "").trim(),
      relationship: (draft.relationship || "").trim(),
      tone: (draft.tone || "").trim(),
      style: (draft.style || "").trim(),
      boundaries: (draft.boundaries || "").trim(),
      response_habits: (draft.response_habits || "")
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
    });
  }

  return (
    <SectionState loading={query.isLoading} error={query.error}>
      <div className="flex flex-col gap-4 pb-4">
        <p className="text-[13px] text-muted px-1 leading-relaxed">
          这些会塑造小布怎么跟你说话。改完记得保存。
        </p>
        <Field label="名字">
          <Input value={draft.name || ""} onChange={(e) => set("name", e.target.value)} placeholder="小布" />
        </Field>
        <Field label="怎么称呼你">
          <Input value={draft.address_user || ""} onChange={(e) => set("address_user", e.target.value)} placeholder="你 / 阿航…" />
        </Field>
        <Field label="和你的关系">
          <Textarea rows={2} value={draft.relationship || ""} onChange={(e) => set("relationship", e.target.value)} />
        </Field>
        <Field label="语气">
          <Textarea rows={3} value={draft.tone || ""} onChange={(e) => set("tone", e.target.value)} />
        </Field>
        <Field label="整体风格">
          <Textarea rows={3} value={draft.style || ""} onChange={(e) => set("style", e.target.value)} />
        </Field>
        <Field label="回应习惯" hint="一行一条">
          <Textarea rows={5} value={draft.response_habits || ""} onChange={(e) => set("response_habits", e.target.value)} />
        </Field>
        <Field label="边界">
          <Textarea rows={3} value={draft.boundaries || ""} onChange={(e) => set("boundaries", e.target.value)} />
        </Field>
      </div>

      <div className="sticky bottom-0 -mx-4 px-4 py-3 glass border-t border-line">
        <Button className="w-full" disabled={!dirty || mutation.isPending} onClick={save}>
          <Save size={16} />
          {mutation.isPending ? "保存中…" : dirty ? "保存" : "已同步"}
        </Button>
      </div>
    </SectionState>
  );
}
