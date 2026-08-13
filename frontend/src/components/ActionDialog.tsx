import { useState } from "react";

export type ActionField = {
  name: string;
  label: string;
  type?: "text" | "textarea" | "number" | "select" | "datetime-local";
  required?: boolean;
  initial?: string;
  options?: Array<{ value: string; label: string }>;
};

type Props = {
  title: string;
  fields?: ActionField[];
  confirmLabel?: string;
  onConfirm: (values: Record<string, string>) => Promise<void>;
  onClose: () => void;
};

export function ActionDialog({ title, fields = [], confirmLabel = "确认操作", onConfirm, onClose }: Props) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((field) => [field.name, field.initial ?? field.options?.[0]?.value ?? ""])));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function submit() {
    if (fields.some((field) => field.required && !values[field.name]?.trim())) { setError("请填写所有必填项。"); return; }
    setPending(true); setError("");
    try { await onConfirm(values); onClose(); } catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); } finally { setPending(false); }
  }
  return <div className="dialog-backdrop" role="presentation"><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="action-title"><span className="eyebrow">业务操作</span><h2 id="action-title">{title}</h2><div className="dialog-form">{fields.map((field) => <label key={field.name}>{field.label}{field.type === "textarea" ? <textarea required={field.required} value={values[field.name]} onChange={(event) => setValues({ ...values, [field.name]: event.target.value })} /> : field.type === "select" ? <select value={values[field.name]} onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}>{field.options?.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select> : <input type={field.type ?? "text"} required={field.required} value={values[field.name]} onChange={(event) => setValues({ ...values, [field.name]: event.target.value })} />}</label>)}</div>{error && <p className="inline-error" role="alert">{error}</p>}<div className="dialog-actions"><button className="button ghost" onClick={onClose} disabled={pending}>取消</button><button className="button primary" onClick={() => void submit()} disabled={pending}>{pending ? "处理中…" : confirmLabel}</button></div></section></div>;
}
