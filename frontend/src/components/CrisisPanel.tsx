import { Shield, Phone, Heart, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { fetchSafetyResources } from "../lib/api";
import { Sheet } from "./Sheet";

type CrisisResource = {
  title: string;
  phone: string;
  description?: string;
};

const fallbackResources: CrisisResource[] = [
  { title: "北京心理危机研究与干预中心", phone: "010-82951332" },
  { title: "希望24热线（全国）", phone: "400-161-9995", description: "24小时危机干预" },
  { title: "生命热线", phone: "400-821-1215" },
  { title: "紧急情况", phone: "110 / 120", description: "如遇立即危险请拨打" },
];

type Props = {
  open: boolean;
  onClose: () => void;
};

export function CrisisPanel({ open, onClose }: Props) {
  const resourcesQuery = useQuery({
    queryKey: ["crisis-resources"],
    queryFn: () => fetchSafetyResources().catch(() => null),
    staleTime: 1000 * 60 * 60,
  });

  const resources: CrisisResource[] = resourcesQuery.data?.hotlines || fallbackResources;

  return (
    <Sheet open={open} onClose={onClose} title="危机资源" side="left">
      <div className="flex flex-col gap-4 p-1">
        <div className="flex items-start gap-3 rounded-xl bg-negative/8 border border-negative/20 px-4 py-3">
          <Shield size={18} strokeWidth={1.8} className="text-negative shrink-0 mt-0.5" />
          <div>
            <p className="text-[13px] font-medium text-ink">
              如果你正在考虑伤害自己
            </p>
            <p className="text-[12px] text-ink-soft mt-0.5">
              请现在就联系身边可信任的人，或拨打下面的热线。你不需要独自面对。
            </p>
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-[11.5px] font-semibold uppercase tracking-wide text-faint px-1">
            热线电话
          </p>
          {resources.map((r) => (
            <a
              key={`${r.title}-${r.phone}`}
              href={`tel:${r.phone.replace(/[^\d]/g, "")}`}
              className="flex items-center gap-3 rounded-xl bg-surface border border-line px-4 py-3 hover:bg-surface-2 transition-colors touch-target"
            >
              <Phone size={16} strokeWidth={1.8} className="text-accent shrink-0" />
              <div className="min-w-0">
                <p className="text-[13.5px] font-medium text-ink">{r.title}</p>
                <p className="text-[15px] font-semibold text-accent mt-0.5">{r.phone}</p>
                {r.description ? (
                  <p className="text-[11.5px] text-muted mt-0.5">{r.description}</p>
                ) : null}
              </div>
            </a>
          ))}
        </div>

        <div className="rounded-xl bg-surface-2 border border-line px-4 py-3">
          <p className="text-[12px] font-medium text-ink-soft mb-1.5">
            <Heart size={13} strokeWidth={1.8} className="inline text-negative mr-1" />
            快速接地练习
          </p>
          <p className="text-[12px] text-muted leading-relaxed">
            看看周围，在心里默念：你看到的 5 样东西、你感受到的 4 样触感、你听到的 3 种声音、你闻到的 2 种气味、你尝到的 1 种味道。
          </p>
        </div>

        <p className="text-[11px] text-faint text-center">
          MyBuddy 不是危机服务，以上资源由公开信息整理。
        </p>
      </div>
    </Sheet>
  );
}
