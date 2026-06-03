import { Archive, RotateCcw } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchSkills, updateSkill } from "../api/client";
import { ConfidenceMeter, EmptyState, ErrorState, LoadingState, PageHeader, Panel, Tags } from "../components/Primitives";
import { queryKeys } from "../state/observability";
import type { Skill } from "../types/api";

export function SkillsView() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.skills, queryFn: fetchSkills });
  const mutation = useMutation({
    mutationFn: ({ name, archived }: { name: string; archived: boolean }) => updateSkill(name, archived),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.skills }),
  });

  if (query.isLoading) return <LoadingState label="正在读取 Skills" />;
  if (query.error) return <ErrorState error={query.error} />;
  const skills = [...(query.data?.skills || [])].sort((a, b) => b.confidence - a.confidence);
  const active = skills.filter((skill) => !skill.archived);
  const archived = skills.filter((skill) => skill.archived);

  return (
    <section className="view skills-view">
      <PageHeader description="归档不会删除 Skill，只是不再让它参与触发排序。" title="Skills" />
      <div className="dashboard-grid">
        <Panel title="活跃 Skills" description={`${active.length} 个可触发技能`}>
          {active.length ? (
            <div className="table-list">
              {active.map((skill) => (
                <SkillRow key={skill.name} mutationPending={mutation.isPending} skill={skill} toggle={mutation.mutate} />
              ))}
            </div>
          ) : (
            <EmptyState title="没有活跃 Skill" text="恢复归档项后，它会重新进入触发候选。" />
          )}
        </Panel>

        <Panel title="归档" description={`${archived.length} 个已归档技能`}>
          {archived.length ? (
            <div className="table-list">
              {archived.map((skill) => (
                <SkillRow key={skill.name} mutationPending={mutation.isPending} skill={skill} toggle={mutation.mutate} />
              ))}
            </div>
          ) : (
            <EmptyState title="归档为空" text="不需要的 Skill 可以在活跃列表中归档。" />
          )}
        </Panel>
      </div>
    </section>
  );
}

function SkillRow({
  skill,
  mutationPending,
  toggle,
}: {
  skill: Skill;
  mutationPending: boolean;
  toggle: (input: { name: string; archived: boolean }) => void;
}) {
  return (
    <article className="skill-row">
      <div>
        <strong>{skill.name}</strong>
        <Tags values={skill.triggers} />
      </div>
      <ConfidenceMeter value={skill.confidence} />
      <span className="skill-ratio">
        {skill.success_count} / {skill.fail_count}
      </span>
      <span className={skill.archived ? "badge cancelled" : "badge pending"}>
        {skill.archived ? "archived" : "active"}
      </span>
      <button
        aria-label={skill.archived ? "恢复 Skill" : "归档 Skill"}
        className="icon-button"
        data-state={mutationPending ? "loading" : undefined}
        disabled={mutationPending}
        onClick={() => toggle({ name: skill.name, archived: !skill.archived })}
        title={skill.archived ? "恢复 Skill" : "归档 Skill"}
        type="button"
      >
        {skill.archived ? <RotateCcw size={15} /> : <Archive size={15} />}
      </button>
    </article>
  );
}
