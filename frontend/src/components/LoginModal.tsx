import React, { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  AlertCircle,
  CheckCircle2,
  KeyRound,
  Lock,
  RefreshCw,
  ShieldCheck,
  Smartphone,
  X,
} from "lucide-react";
import type { AuthSummary } from "../types";

interface LoginModalProps {
  open: boolean;
  auth: AuthSummary;
  deptIdEnc: string;
  onClose: () => void;
  onLogin: (params: {
    phone: string;
    password: string;
    deptIdEnc?: string;
  }) => Promise<{ success: boolean; message: string }>;
  onCheckSession: () => Promise<void>;
}

export const LoginModal: React.FC<LoginModalProps> = ({
  open,
  auth,
  deptIdEnc,
  onClose,
  onLogin,
  onCheckSession,
}) => {
  const [phone, setPhone] = useState(auth.phone || "");
  const [password, setPassword] = useState("");
  const [deptId, setDeptId] = useState(deptIdEnc || "df23c42633fa45e1");
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState(false);
  const [feedback, setFeedback] = useState<{
    ok: boolean;
    text: string;
  } | null>(null);

  useEffect(() => {
    if (open) {
      setPhone(auth.phone || "");
      setDeptId(deptIdEnc || "df23c42633fa45e1");
      setFeedback(null);
    }
  }, [open, auth.phone, deptIdEnc]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!phone.trim() || !password) {
      setFeedback({ ok: false, text: "请输入完整的超星学习通手机号与密码" });
      return;
    }
    setSubmitting(true);
    setFeedback(null);
    try {
      const res = await onLogin({
        phone: phone.trim(),
        password,
        deptIdEnc: deptId.trim() || undefined,
      });
      setFeedback({
        ok: res.success,
        text: res.message || (res.success ? "登录成功，会话凭据已持久化" : "登录未通过"),
      });
      if (res.success) {
        setPassword("");
        window.setTimeout(() => {
          onClose();
        }, 700);
      }
    } catch (err) {
      setFeedback({
        ok: false,
        text: err instanceof Error ? err.message : "登录请求异常",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleVerifySession = async () => {
    setChecking(true);
    setFeedback(null);
    try {
      await onCheckSession();
      setFeedback({
        ok: true,
        text: "已同步最新会话状态",
      });
    } catch (err) {
      setFeedback({
        ok: false,
        text: err instanceof Error ? err.message : "会话检测失败",
      });
    } finally {
      setChecking(false);
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          onClick={onClose}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/25 p-4 backdrop-blur-sm"
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.94, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 8 }}
            transition={{ type: "spring", stiffness: 440, damping: 32 }}
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-[400px] overflow-hidden rounded-2xl border border-black/[0.08] bg-white/92 shadow-[0_24px_60px_rgba(0,0,0,0.16)] backdrop-blur-2xl"
          >
            {/* Header */}
            <div className="flex items-start justify-between border-b border-black/[0.06] px-6 pt-5 pb-4">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#0071E3]/10 text-[#0071E3]">
                  <KeyRound className="h-4.5 w-4.5 stroke-[1.75]" />
                </div>
                <div>
                  <h2 className="text-[15px] font-semibold tracking-tight text-[#1D1D1F]">
                    超星图书馆账号凭据
                  </h2>
                  <p className="text-[12px] text-[#86868B]">
                    凭据仅加密保存于本地工作目录，用于自动预热与选座
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="rounded-full p-1 text-[#86868B] transition hover:bg-black/[0.06] hover:text-[#1D1D1F]"
                aria-label="关闭窗口"
              >
                <X className="h-4 w-4 stroke-[1.75]" />
              </button>
            </div>

            {/* Current Session Banner */}
            <div className="mx-6 mt-4 flex items-center justify-between rounded-xl border border-black/[0.06] bg-[#F5F5F7] px-3.5 py-2.5">
              <div className="flex items-center gap-2">
                <span
                  className={`h-2 w-2 rounded-full ${
                    auth.loggedIn ? "bg-[#34C759]" : "bg-[#FF9500]"
                  }`}
                />
                <span className="text-[12px] font-medium text-[#1D1D1F] tabular-nums">
                  {auth.loggedIn
                    ? `当前在线 · ${auth.phoneMasked || "已授权会话"}`
                    : auth.phoneMasked
                      ? `待刷新会话 · ${auth.phoneMasked}`
                      : "尚未配置账号"}
                </span>
              </div>
              <button
                type="button"
                onClick={handleVerifySession}
                disabled={checking}
                className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-1 text-[11px] font-medium text-[#1D1D1F] shadow-2xs ring-1 ring-black/[0.06] transition hover:bg-black/[0.02] disabled:opacity-50"
              >
                <RefreshCw
                  className={`h-3 w-3 stroke-[1.75] ${
                    checking ? "animate-spin text-[#0071E3]" : "text-[#86868B]"
                  }`}
                />
                <span>检测会话</span>
              </button>
            </div>

            {/* Form */}
            <form onSubmit={handleSubmit} className="space-y-3.5 px-6 pt-4 pb-6">
              <div className="space-y-1.5">
                <label className="block text-[12px] font-medium text-[#1D1D1F]">
                  手机号码
                </label>
                <div className="relative">
                  <Smartphone className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 stroke-[1.6] text-[#86868B]" />
                  <input
                    type="tel"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    placeholder="输入学习通绑定手机号"
                    autoComplete="username"
                    className="h-9.5 w-full rounded-xl border border-black/[0.1] bg-white pr-3 pl-9 text-[13px] text-[#1D1D1F] tabular-nums transition focus:border-[#0071E3] focus:ring-3 focus:ring-[#0071E3]/15 focus:outline-none"
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label className="block text-[12px] font-medium text-[#1D1D1F]">
                  登录密码
                </label>
                <div className="relative">
                  <Lock className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 stroke-[1.6] text-[#86868B]" />
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={
                      auth.hasPassword
                        ? "已保存密码，输入新密码可重新登录刷新会话"
                        : "输入超星学习通密码"
                    }
                    autoComplete="current-password"
                    className="h-9.5 w-full rounded-xl border border-black/[0.1] bg-white pr-3 pl-9 text-[13px] text-[#1D1D1F] transition focus:border-[#0071E3] focus:ring-3 focus:ring-[#0071E3]/15 focus:outline-none"
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label className="flex items-center justify-between text-[11px] font-medium text-[#86868B]">
                  <span>场馆机构编码 (deptIdEnc)</span>
                  <span className="font-mono-code text-[10px]">默认无需修改</span>
                </label>
                <input
                  type="text"
                  value={deptId}
                  onChange={(e) => setDeptId(e.target.value)}
                  className="font-mono-code h-8.5 w-full rounded-lg border border-black/[0.08] bg-[#F5F5F7] px-3 text-[12px] text-[#1D1D1F] transition focus:border-[#0071E3] focus:bg-white focus:outline-none"
                />
              </div>

              {feedback && (
                <div
                  className={`flex items-start gap-2 rounded-xl px-3 py-2 text-[12px] ${
                    feedback.ok
                      ? "border border-[#34C759]/25 bg-[#34C759]/10 text-[#1D1D1F]"
                      : "border border-[#FF3B30]/25 bg-[#FF3B30]/10 text-[#FF3B30]"
                  }`}
                >
                  {feedback.ok ? (
                    <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 stroke-[1.8] text-[#34C759]" />
                  ) : (
                    <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 stroke-[1.8] text-[#FF3B30]" />
                  )}
                  <span className="leading-snug">{feedback.text}</span>
                </div>
              )}

              <div className="flex items-center justify-end gap-2.5 pt-2">
                <button
                  type="button"
                  onClick={onClose}
                  className="h-9 rounded-xl border border-black/[0.08] bg-white px-4 text-[12.5px] font-medium text-[#1D1D1F] transition hover:bg-black/[0.03]"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-[#0071E3] px-4.5 text-[12.5px] font-medium text-white shadow-xs transition hover:bg-[#0077ED] active:scale-[0.98] disabled:opacity-60"
                >
                  {submitting ? (
                    <>
                      <RefreshCw className="h-3.5 w-3.5 animate-spin stroke-[1.75]" />
                      <span>正在认证并持久化会话...</span>
                    </>
                  ) : (
                    <>
                      <ShieldCheck className="h-3.5 w-3.5 stroke-[1.75]" />
                      <span>登录并更新凭据</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};
