export function EnvironmentBadge() {
  const label = import.meta.env.VITE_ENV_LABEL as string | undefined;
  if (!label || label.toLowerCase() === "production") return null;
  return <div className="environment-badge">{label}环境 · 数据仅供演示</div>;
}
