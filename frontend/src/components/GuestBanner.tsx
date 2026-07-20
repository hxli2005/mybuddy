import { LogIn, MessageSquarePlus } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "../lib/router";
import { resetChatContext } from "../lib/api";
import { clearGuestMessages } from "../lib/guestStorage";

type Props = {
  className?: string;
};

export function GuestBanner({ className }: Props) {
  const { navigate } = useRouter();
  const queryClient = useQueryClient();

  function handleNewChat() {
    clearGuestMessages();
    resetChatContext().catch(() => {});
    queryClient.invalidateQueries();
    window.dispatchEvent(new Event("mybuddy:data-cleared"));
  }

  return (
    <div className={`guest-banner ${className || ""}`}>
      <div className="mx-auto w-full max-w-2xl px-4 sm:px-5 py-2 flex items-center justify-between gap-3">
        <p className="text-[12px] text-ink-soft">
          你正在以访客身份使用，对话记录和情绪数据不会被保存
        </p>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={handleNewChat}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-ink-soft hover:text-ink transition-colors touch-target"
          >
            <MessageSquarePlus size={13} />
            新对话
          </button>
          <button
            type="button"
            onClick={() => navigate("login")}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-accent hover:underline touch-target"
          >
            <LogIn size={13} />
            登录 / 注册
          </button>
        </div>
      </div>
    </div>
  );
}
