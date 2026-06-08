import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, RotateCcw, Wand2 } from "lucide-react";
import { fetchSkills, updateSkill } from "../../lib/api";
import { EmptyState, IconButton, SectionLabel } from "../../components/ui";
import { queryKeys } from "../../lib/queryKeys";
import { ConfidenceBar, ItemCard, MiniTags, SectionState } from "./common";
import type { Skill } from "../../types/api";

export function SkillsSection() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.skills, queryFn: fetchSkills });
  const mutation = useMutation({
    mutationFn: ({ name, archived }: { name: string; archived: boolean }) => updateSkill(name, archived),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.skills }),
  });

  const skills = [...(query.data?.skills || [])].sort((a, b) => b.confidence - a.confidence);
  const active = skills.filter((s) => !s.archived);
  const archived = skills.filter((s) => s.archived);

  return (
    <SectionState loading={query.isLoading} error={query.error}>
      <div className="flex flex-col gap-5">
        <p className="text-[13px] text-muted px-1 leading-relaxed">
          这些是小布自己从聊天里总结出的应对习惯，用得好会更可靠，太糟会自动收起。
        </p>

        <div className="flex flex-col gap-2.5">
          <SectionLabel>在用 · {active.length}</SectionLabel>
          {active.length ? (
            active.map((s) => (
              <SkillCard key={s.name} skill={s} disabled={mutation.isPending} onToggle={() => mutation.mutate({ name: s.name, archived: true })} />
            ))
          ) : (
            <EmptyState icon={Wand2} title="还没学会什么" text="多聊几次复杂任务，小布会慢慢攒出习惯。" />
          )}
        </div>

        {archived.length ? (
          <div className="flex flex-col gap-2.5">
            <SectionLabel>已收起 · {archived.length}</SectionLabel>
            {archived.map((s) => (
              <SkillCard key={s.name} skill={s} archived disabled={mutation.isPending} onToggle={() => mutation.mutate({ name: s.name, archived: false })} />
            ))}
          </div>
        ) : null}
      </div>
    </SectionState>
  );
}

function SkillCard({
  skill,
  archived,
  onToggle,
  disabled,
}: {
  skill: Skill;
  archived?: boolean;
  onToggle: () => void;
  disabled: boolean;
}) {
  return (
    <ItemCard>
      <div className="flex items-start justify-between gap-2">
        <strong className="text-[14px] font-semibold text-ink">{skill.name}</strong>
        <IconButton
          icon={archived ? RotateCcw : Archive}
          label={archived ? "恢复" : "收起"}
          size={15}
          onClick={onToggle}
          disabled={disabled}
        />
      </div>
      <MiniTags values={skill.triggers} />
      <div className="flex items-center justify-between gap-2">
        <ConfidenceBar value={skill.confidence} />
        <span className="text-[11.5px] text-faint tabular-nums">
          成功 {skill.success_count} · 失败 {skill.fail_count}
        </span>
      </div>
    </ItemCard>
  );
}
