import { Heart, Bell, Wand2, Download, Trash2, RefreshCw, UserX } from "lucide-react";
import { Surface, SectionLabel, Divider } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { useRouter } from "../../lib/router";
import {
  clearUserData,
  deleteAccount,
  exportUserData,
  resetAssessment,
  resetChatContext,
} from "../../lib/api";
import { clearGuestMessages } from "../../lib/guestStorage";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

type Props = {
  settings: MentalHealthSettings;
  onChange: (settings: MentalHealthSettings) => void;
};

export type MentalHealthSettings = {
  checkinReminder: boolean;
  cbtSuggestions: boolean;
  statusReminder: boolean;
};

export function MentalHealthSection({ settings, onChange }: Props) {
  const { isLoggedIn, logout } = useAuth();
  const { navigate } = useRouter();
  const queryClient = useQueryClient();
  const [confirmReset, setConfirmReset] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  function toggle(key: keyof MentalHealthSettings) {
    onChange({ ...settings, [key]: !settings[key] });
  }

  function invalidateAll() {
    for (const key of [["messages"], ["mood-records"], ["mood-stats"], ["assessment-status"], ["assessment-history"]]) {
      queryClient.invalidateQueries({ queryKey: key });
    }
  }

  function handleExport() {
    exportUserData()
      .then((data) => {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `mybuddy-data-${new Date().toISOString().slice(0, 10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
      })
      .catch(() => setMessage("导出失败"));
  }

  function handleClearData() {
    if (!confirmClear) {
      setConfirmClear(true);
      return;
    }
    setBusy(true);
    setMessage("");
    if (isLoggedIn) {
      clearUserData()
        .then(() => {
          invalidateAll();
          window.dispatchEvent(new Event("mybuddy:data-cleared"));
          setMessage("聊天记录与数据已全部清除");
          setConfirmClear(false);
        })
        .catch(() => setMessage("清除失败"))
        .finally(() => setBusy(false));
    } else {
      try {
        clearGuestMessages();
        const keys = Object.keys(localStorage).filter((k) => k.startsWith("mybuddy-"));
        keys.forEach((k) => localStorage.removeItem(k));
        resetChatContext().catch(() => {});
        invalidateAll();
        window.dispatchEvent(new Event("mybuddy:data-cleared"));
        setMessage("聊天记录与本地数据已清除");
        setConfirmClear(false);
      } catch {
        setMessage("清除失败");
      }
      setBusy(false);
    }
  }

  function handleDeleteAccount() {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setBusy(true);
    setMessage("");
    deleteAccount()
      .then(async () => {
        invalidateAll();
        await logout();
        navigate("login");
      })
      .catch(() => setMessage("删除账户失败"))
      .finally(() => setBusy(false));
  }

  return (
    <div className="flex flex-col gap-5">
      <SectionLabel>心理健康</SectionLabel>

      {/* 提醒设置:访客仅显示 CBT 建议开关 */}
      <Surface className="p-4 flex flex-col gap-3">
        {isLoggedIn ? (
          <>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Bell size={17} strokeWidth={1.8} className="text-muted" />
                <div>
                  <p className="text-[13.5px] font-medium text-ink">每日签到提醒</p>
                  <p className="text-[11.5px] text-muted">每天第一次打开聊天时轻提示签到</p>
                </div>
              </div>
              <Toggle checked={settings.checkinReminder} onChange={() => toggle("checkinReminder")} />
            </div>

            <Divider />
          </>
        ) : null}

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <Wand2 size={17} strokeWidth={1.8} className="text-muted" />
            <div>
              <p className="text-[13.5px] font-medium text-ink">CBT 技巧建议</p>
              <p className="text-[11.5px] text-muted">聊天中自然地引入放松练习或思维小技巧</p>
            </div>
          </div>
          <Toggle checked={settings.cbtSuggestions} onChange={() => toggle("cbtSuggestions")} />
        </div>

        {isLoggedIn ? (
          <>
            <Divider />

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Heart size={17} strokeWidth={1.8} className="text-muted" />
                <div>
                  <p className="text-[13.5px] font-medium text-ink">状态提醒</p>
                  <p className="text-[11.5px] text-muted">评估周期完成时温和提示查看结果</p>
                </div>
              </div>
              <Toggle checked={settings.statusReminder} onChange={() => toggle("statusReminder")} />
            </div>
          </>
        ) : null}
      </Surface>

      {/* 数据管理(仅登录用户) */}
      {isLoggedIn ? (
        <Surface className="p-4 flex flex-col gap-3">
          <p className="text-[12.5px] font-medium text-ink">数据管理</p>

          <button
            type="button"
            onClick={handleExport}
            className="flex items-center gap-2.5 text-[13px] text-ink-soft hover:text-ink transition-colors py-1 touch-target"
          >
            <Download size={16} strokeWidth={1.8} />
            导出全部个人数据 (JSON)
          </button>

          <Divider />

          <button
            type="button"
            onClick={() => {
              if (!confirmReset) {
                setConfirmReset(true);
                return;
              }
              resetAssessment()
                .then(() => {
                  setConfirmReset(false);
                  queryClient.invalidateQueries({ queryKey: ["assessment-status"] });
                })
                .catch(() => {});
            }}
            className="flex items-center gap-2.5 text-[13px] text-negative hover:underline transition-colors py-1 touch-target"
          >
            <RefreshCw size={16} strokeWidth={1.8} />
            {confirmReset ? "确认重置评估周期？再次点击确认" : "重置评估周期"}
          </button>
        </Surface>
      ) : null}

      {/* 清除数据 / 删除账户 */}
      <Surface className="p-4 flex flex-col gap-3">
        <p className="text-[12.5px] font-medium text-ink">
          {isLoggedIn ? "清除账号数据" : "清除本地数据"}
        </p>
        <p className="text-[11.5px] text-muted">
          {isLoggedIn
            ? "删除所有聊天记录、情绪数据、评估记录。此操作不可撤销。"
            : "清除浏览器中的聊天记录和本地数据（主题、设置等）。"}
        </p>
        <button
          type="button"
          onClick={handleClearData}
          disabled={busy}
          className="flex items-center gap-2.5 text-[13px] text-negative hover:underline transition-colors py-1 touch-target"
        >
          <Trash2 size={16} strokeWidth={1.8} />
          {busy ? "处理中…" : confirmClear ? "确认清除？再次点击确认" : (isLoggedIn ? "清除所有数据" : "清除本地数据")}
        </button>

        {isLoggedIn ? (
          <>
            <Divider />
            <button
              type="button"
              onClick={handleDeleteAccount}
              disabled={busy}
              className="flex items-center gap-2.5 text-[13px] text-negative hover:underline transition-colors py-1 touch-target"
            >
              <UserX size={16} strokeWidth={1.8} />
              {confirmDelete ? "账户和全部数据将被永久删除，再次点击确认" : "删除账户"}
            </button>
          </>
        ) : null}

        {message ? (
          <p className={`text-[12px] ${message.includes("失败") ? "text-negative" : "text-positive"}`}>
            {message}
          </p>
        ) : null}
      </Surface>

      <p className="text-[11px] text-faint text-center">
        你的数据只存在于本地设备中
      </p>
    </div>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={onChange}
      className={`relative h-8 w-14 rounded-full transition-colors duration-200 ${
        checked ? "bg-accent" : "bg-line-strong"
      }`}
    >
      <span
        className={`absolute top-0.5 h-7 w-7 rounded-full bg-surface shadow-soft transition-transform duration-200 ${
          checked ? "left-[calc(100%-1.875rem)]" : "left-0.5"
        }`}
      />
    </button>
  );
}
