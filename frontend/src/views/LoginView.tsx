import { FormEvent, useState } from "react";
import { Sparkles, Eye, EyeOff } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Field, Surface } from "../components/ui";
import { login as apiLogin, register as apiRegister, importGuestMessages } from "../lib/api";
import { clearGuestMessages, loadGuestMessages } from "../lib/guestStorage";
import { useAuth } from "../lib/auth";
import { useRouter, consumeReturnPage } from "../lib/router";
import { queryKeys } from "../lib/queryKeys";
import { cn } from "../lib/cn";

type Mode = "login" | "register";

export function LoginView() {
  const { login } = useAuth();
  const { navigate } = useRouter();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");

  const authMutation = useMutation({
    mutationFn: async () => {
      const fn = mode === "register" ? apiRegister : apiLogin;
      return fn(username.trim(), password);
    },
    onSuccess: async (data) => {
      login(data.user_id, data.username || username.trim());
      // 访客转登录:询问是否导入进行中的对话
      const guestMessages = loadGuestMessages();
      if (guestMessages.length > 0) {
        const shouldImport = window.confirm(
          "检测到你有进行中的对话，是否导入到账户？\n\n「确定」导入对话记录，「取消」开启空白对话。",
        );
        if (shouldImport) {
          try {
            await importGuestMessages(guestMessages);
          } catch {
            /* 导入失败不阻塞登录 */
          }
        }
        clearGuestMessages();
        queryClient.invalidateQueries({ queryKey: queryKeys.messages });
      }
      navigate(consumeReturnPage());
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : String(err));
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!username.trim()) {
      setError("请输入用户名");
      return;
    }
    if (username.trim().length < 2) {
      setError("用户名至少 2 个字符");
      return;
    }
    if (password.length < 4) {
      setError("密码至少 4 个字符");
      return;
    }
    if (mode === "register" && password !== confirmPassword) {
      setError("两次密码不一致");
      return;
    }
    authMutation.mutate();
  }

  function skipLogin() {
    navigate("chat");
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 flex items-center justify-center px-4 py-8">
        <div className="w-full max-w-sm">
          <div className="text-center mb-6">
            <div className="inline-grid place-items-center h-14 w-14 rounded-2xl bg-accent-soft text-accent mb-3">
              <Sparkles size={26} strokeWidth={1.8} />
            </div>
            <h1 className="text-[17px] font-semibold text-ink">小布</h1>
            <p className="text-[13px] text-muted mt-1">一个懂心理学的温暖朋友</p>
          </div>

          <Surface className="p-5">
            <form onSubmit={onSubmit} className="flex flex-col gap-4">
              <div className="flex rounded-xl bg-surface-2 p-1 gap-1">
                <button
                  type="button"
                  onClick={() => { setMode("login"); setError(""); }}
                  className={cn(
                    "flex-1 text-center py-2 rounded-lg text-[13.5px] font-medium transition-colors",
                    mode === "login" ? "bg-surface text-ink shadow-soft" : "text-muted",
                  )}
                >
                  登录
                </button>
                <button
                  type="button"
                  onClick={() => { setMode("register"); setError(""); }}
                  className={cn(
                    "flex-1 text-center py-2 rounded-lg text-[13.5px] font-medium transition-colors",
                    mode === "register" ? "bg-surface text-ink shadow-soft" : "text-muted",
                  )}
                >
                  注册
                </button>
              </div>

              <Field label="用户名">
                <Input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="2-20 个字符，中文/英文/数字"
                  autoComplete="username"
                  maxLength={20}
                />
              </Field>

              <Field label="密码">
                <div className="relative">
                  <Input
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="至少 4 个字符"
                    autoComplete={mode === "register" ? "new-password" : "current-password"}
                    className="pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted hover:text-ink p-1 touch-target"
                    aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  >
                    {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </Field>

              {mode === "register" ? (
                <Field label="确认密码">
                  <Input
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="再次输入密码"
                    autoComplete="new-password"
                  />
                </Field>
              ) : null}

              {error ? (
                <p className="text-[12.5px] text-negative px-1">{error}</p>
              ) : null}

              <Button type="submit" className="w-full" disabled={authMutation.isPending}>
                {authMutation.isPending ? "请稍等…" : mode === "register" ? "注册并登录" : "登录"}
              </Button>
            </form>
          </Surface>

          <div className="text-center mt-4">
            <button
              type="button"
              onClick={skipLogin}
              className="text-[13px] text-muted hover:text-ink transition-colors py-2 touch-target"
            >
              暂不登录，直接使用
            </button>
          </div>
        </div>
      </div>

      <p className="text-center text-[11px] text-faint pb-4">
        MyBuddy · 本地运行 · 你的数据只在你手中
      </p>
    </div>
  );
}
