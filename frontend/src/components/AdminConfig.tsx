import { useState, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { fetchAdminConfig, updateAdminConfig } from "../lib/api";
import { Surface, Button, Field, Input, Spinner, EmptyState } from "./ui";
import { cn } from "../lib/cn";

const PROVIDERS = [
  { value: "anthropic", label: "Anthropic Claude" },
  { value: "openai", label: "OpenAI" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "deepseek", label: "DeepSeek" },
];

export function AdminConfig() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["adminConfig"],
    queryFn: fetchAdminConfig,
    staleTime: 60_000,
  });

  const [provider, setProvider] = useState("anthropic");
  const [model, setModel] = useState("");
  const [smallModel, setSmallModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [maxTokens, setMaxTokens] = useState("4096");
  const [temperature, setTemperature] = useState("0.7");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!data) return;
    setProvider(data.provider);
    setModel(data.model);
    setSmallModel(data.small_model ?? "");
    setApiKey(data.api_key);
    setBaseUrl(data.base_url ?? "");
    setMaxTokens(String(data.max_tokens));
    setTemperature(String(data.temperature));
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: () =>
      updateAdminConfig({
        provider,
        model,
        small_model: smallModel || null,
        api_key: apiKey.startsWith("***") ? undefined : (apiKey || undefined),
        base_url: baseUrl || null,
        max_tokens: parseInt(maxTokens, 10) || 4096,
        temperature: parseFloat(temperature) || 0.7,
      }),
    onSuccess: () => {
      setSaved(true);
      setTimeout(() => setSaved(false), 4000);
    },
  });

  if (isLoading) {
    return (
      <div className="mx-auto w-full max-w-lg px-4 py-12 flex justify-center">
        <Spinner />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mx-auto w-full max-w-lg px-4 py-12">
        <EmptyState title="加载失败" text="无法获取系统配置" />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-lg px-4 py-6 flex flex-col gap-5">
      <h2 className="text-[13px] font-medium text-muted tracking-wide">AI 模型配置</h2>

      <Surface className="p-5 flex flex-col gap-4">
        <Field label="Provider">
          <div className="flex flex-wrap gap-2">
            {PROVIDERS.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => setProvider(p.value)}
                className={cn(
                  "inline-flex items-center h-8 px-3 rounded-full text-[13px] font-medium transition-colors",
                  provider === p.value
                    ? "bg-accent-soft text-accent"
                    : "bg-surface-2 text-muted hover:text-ink",
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
        </Field>

        <Field label="主对话模型">
          <Input value={model} onChange={(e) => setModel((e.target as HTMLInputElement).value)} placeholder="claude-sonnet-4-5" />
        </Field>

        <Field label="小任务模型 (可选, 记忆/情绪分类等)">
          <Input value={smallModel} onChange={(e) => setSmallModel((e.target as HTMLInputElement).value)} placeholder="留空复用主模型" />
        </Field>

        <Field label="API Key">
          <Input
            value={apiKey}
            onChange={(e) => setApiKey((e.target as HTMLInputElement).value)}
            placeholder={apiKey.startsWith("***") ? "留空则保留原值" : "sk-..."}
          />
          <p className="text-[11px] text-muted mt-1">显示已脱敏;留空提交则不修改原值</p>
        </Field>

        <Field label="Base URL (可选)">
          <Input value={baseUrl} onChange={(e) => setBaseUrl((e.target as HTMLInputElement).value)} placeholder="留空使用默认地址" />
        </Field>

        <Field label="Max Tokens">
          <Input value={maxTokens} onChange={(e) => setMaxTokens((e.target as HTMLInputElement).value)} placeholder="4096" />
        </Field>

        <Field label="Temperature (0-2)">
          <Input value={temperature} onChange={(e) => setTemperature((e.target as HTMLInputElement).value)} placeholder="0.7" />
        </Field>

        <div className="flex items-center gap-3 pt-2">
          <Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending} className="whitespace-nowrap shrink-0">
            {saveMutation.isPending ? <Spinner /> : "保存配置"}
          </Button>
          {saved && (
            <span className="text-[13px] text-positive animate-fade-in">
              配置已保存，需重启服务生效
            </span>
          )}
        </div>
      </Surface>
    </div>
  );
}
