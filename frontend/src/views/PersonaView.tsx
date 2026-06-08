import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchPersona, savePersona } from "../api/client";
import { PersonaEditor } from "../components/PersonaEditor";
import { ErrorState, LoadingState, PageHeader } from "../components/Primitives";
import { queryKeys } from "../state/observability";

export function PersonaView() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.persona, queryFn: fetchPersona });
  const mutation = useMutation({
    mutationFn: savePersona,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.persona });
      queryClient.invalidateQueries({ queryKey: queryKeys.status });
    },
  });

  if (query.isLoading) return <LoadingState label="正在读取人格" />;
  if (query.error) return <ErrorState error={query.error} />;

  return (
    <section className="view persona-view">
      <PageHeader description="人格配置决定回复的边界、称呼和习惯，不直接写入记忆。" title="人格" />
      <PersonaEditor
        error={mutation.error}
        formId="persona-form"
        onSave={(persona) => mutation.mutate(persona)}
        persona={query.data?.persona || {}}
        saving={mutation.isPending}
      />
    </section>
  );
}
