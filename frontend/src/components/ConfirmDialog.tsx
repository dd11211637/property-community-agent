import { useState, type ReactNode } from "react";

type Props = {
  title: string;
  summary: ReactNode;
  confirmLabel?: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
  onCancel?: () => void;
};

export function ConfirmDialog({ title, summary, confirmLabel = "确认提交", onConfirm, onClose, onCancel = onClose }: Props) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const confirm = async () => {
    setPending(true);
    setError("");
    try {
      await onConfirm();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提交失败");
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <span className="eyebrow">请核对操作</span>
        <h2 id="confirm-title">{title}</h2>
        <div className="confirm-summary">{summary}</div>
        {error && <p className="inline-error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <button className="button ghost" onClick={onCancel} disabled={pending}>取消</button>
          <button className="button primary" onClick={confirm} disabled={pending}>{pending ? "提交中…" : confirmLabel}</button>
        </div>
      </section>
    </div>
  );
}
