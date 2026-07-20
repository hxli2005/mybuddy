import { LogIn, User, LogOut } from "lucide-react";
import { useAuth } from "../lib/auth";
import { useRouter } from "../lib/router";

export function UserMenu() {
  const { isLoggedIn, username, logout } = useAuth();
  const { navigate } = useRouter();

  if (isLoggedIn) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-[12.5px] text-ink-soft hidden sm:inline">{username}</span>
        <button
          type="button"
          onClick={() => logout()}
          aria-label="退出登录"
          title="退出登录"
          className="inline-grid place-items-center h-10 w-10 rounded-full text-muted hover:text-ink hover:bg-surface-2 transition-all duration-200 active:scale-95 touch-target"
        >
          <LogOut size={17} strokeWidth={1.9} />
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => navigate("login")}
      aria-label="登录"
      title="登录"
      className="inline-flex items-center gap-1.5 h-10 px-3 rounded-full text-[13px] font-medium text-accent hover:bg-accent-soft transition-all duration-200 active:scale-95 touch-target"
    >
      <LogIn size={16} strokeWidth={1.9} />
      <span className="hidden sm:inline">登录</span>
    </button>
  );
}
