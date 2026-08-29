import { useState, type FormEvent, type ReactNode } from "react";
import {
  Badge,
  Button,
  Card,
  ErrorState,
  Field,
  InlineAlert,
  Input,
  LoadingState,
} from "../shared/ui";
import styles from "../styles/business.module.css";
import { describeBusinessError } from "./hooks";

export function BusinessHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className={styles.header}>
      <div>
        <span className={styles.eyebrow}>{eyebrow}</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </header>
  );
}

export function QueryBoundary({
  pending,
  error,
  empty,
  children,
}: {
  pending: boolean;
  error: unknown;
  empty: boolean;
  children: ReactNode;
}) {
  if (pending) return <LoadingState label="正在读取真实业务数据" />;
  if (error) return <ErrorState description={describeBusinessError(error)} />;
  if (empty)
    return <div className={styles.empty}>当前真实作用域内没有记录。</div>;
  return <>{children}</>;
}

export function DetailGrid({
  entries,
}: {
  entries: readonly [string, ReactNode][];
}) {
  return (
    <dl className={styles.details}>
      {entries.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value || "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function MutationNotice({
  error,
  success,
}: {
  error: unknown;
  success?: string;
}) {
  return (
    <>
      {error ? <InlineAlert>{describeBusinessError(error)}</InlineAlert> : null}
      {success ? <InlineAlert>{success}</InlineAlert> : null}
    </>
  );
}

export type ActionField = {
  name: string;
  label: string;
  kind?: "text" | "number" | "datetime";
  required?: boolean;
  defaultValue?: string;
  choices?: { value: string; label: string }[];
};
export type ActionSpec = {
  code: string;
  label: string;
  destructive?: boolean;
  fields?: ActionField[];
};

export function ActionWorkbench({
  actions,
  busy,
  error,
  onSubmit,
}: {
  actions: ActionSpec[];
  busy: boolean;
  error: unknown;
  onSubmit(action: string, values: Record<string, string>): Promise<void>;
}) {
  const [active, setActive] = useState<ActionSpec | null>(null);
  const [success, setSuccess] = useState("");
  if (!actions.length)
    return (
      <Card>
        <strong>当前没有可执行操作</strong>
        <p>操作由服务端状态、角色与 available_actions 共同决定。</p>
      </Card>
    );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active) return;
    setSuccess("");
    const values = Object.fromEntries(
      new FormData(event.currentTarget).entries(),
    ) as Record<string, string>;
    await onSubmit(active.code, values);
    setSuccess("操作已由服务端确认并提交。");
    setActive(null);
  }
  return (
    <Card>
      <div className={styles.actionHeader}>
        <div>
          <strong>可执行操作</strong>
          <p>提交后以服务端返回状态为准。</p>
        </div>
        <div className={styles.actions}>
          {actions.map((action) => (
            <Button
              key={action.code}
              tone={action.destructive ? "danger" : "secondary"}
              onClick={() => {
                setActive(action);
                setSuccess("");
              }}
            >
              {action.label}
            </Button>
          ))}
        </div>
      </div>
      {active ? (
        <form
          className={styles.form}
          onSubmit={(event) => void submit(event)}
          aria-label={`${active.label}表单`}
        >
          <h3>{active.label}</h3>
          {active.fields?.map((field) => (
            <Field key={field.name} label={field.label}>
              {field.choices ? (
                <select
                  className="business-select"
                  name={field.name}
                  required={field.required}
                  defaultValue={field.defaultValue ?? ""}
                >
                  <option value="">请选择</option>
                  {field.choices.map((choice) => (
                    <option key={choice.value} value={choice.value}>
                      {choice.label}
                    </option>
                  ))}
                </select>
              ) : (
                <Input
                  name={field.name}
                  type={
                    field.kind === "number"
                      ? "number"
                      : field.kind === "datetime"
                        ? "datetime-local"
                        : "text"
                  }
                  defaultValue={field.defaultValue}
                  required={field.required}
                />
              )}
            </Field>
          ))}
          <div className={styles.actions}>
            <Button type="button" onClick={() => setActive(null)}>
              取消
            </Button>
            <Button tone="primary" type="submit" disabled={busy}>
              {busy ? "正在提交…" : "确认提交"}
            </Button>
          </div>
        </form>
      ) : null}
      <MutationNotice error={error} success={success} />
    </Card>
  );
}

export function StatusBadge({ value }: { value: string }) {
  return <Badge>{value.replaceAll("_", " ")}</Badge>;
}
