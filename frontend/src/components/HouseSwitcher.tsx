import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import type { WorkspaceKind } from "../ui/roles";

export function HouseSwitcher({ workspace }: { workspace: WorkspaceKind }) {
  const { session, selectHouse } = useAuth();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const houses = session?.houses ?? [];
  const current = houses.find((house) => house.id === session?.current_house_id);

  if (houses.length <= 1) {
    const label = current?.label ?? (workspace === "resident" ? "尚未绑定房屋" : "社区级服务");
    return <div className="house-picker house-picker-static">
      <small>{current ? "当前房屋 · 仅一套" : "服务范围"}</small>
      <strong aria-label="当前房屋">{label}</strong>
    </div>;
  }

  const changeHouse = async (houseId: string) => {
    const house = houses.find((item) => item.id === houseId);
    if (!house || house.id === session?.current_house_id) return;
    setPending(true);
    setError("");
    try { await selectHouse(house); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "房屋切换失败，请重试。"); }
    finally { setPending(false); }
  };

  return <div className="house-picker">
    <small>{pending ? "正在切换房屋…" : "切换房屋"}</small>
    <select
      aria-label="当前房屋"
      aria-describedby={!session?.current_house_id ? "house-selection-help" : undefined}
      aria-busy={pending}
      autoFocus={!session?.current_house_id}
      disabled={pending}
      value={session?.current_house_id ?? ""}
      onChange={(event) => void changeHouse(event.target.value)}
    >
      {!session?.current_house_id && <option value="">请选择房屋</option>}
      {houses.map((house) => <option value={house.id} key={house.id}>{house.label}</option>)}
    </select>
    {error && <small className="house-picker-error" role="alert">{error}</small>}
  </div>;
}
