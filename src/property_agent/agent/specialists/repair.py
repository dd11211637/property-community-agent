"""Canonical stateless Repair specialist."""

from datetime import datetime

from property_agent.agent.orchestration import SpecialistName
from property_agent.agent.specialists.base import StatelessSpecialist


def _parse_appointment_at(value: object) -> datetime | None:
    """Parse a user-supplied appointment time; return None if it is not a datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text or text in {"稍后协商", "待定", "未知"}:
        return None
    # Accept ISO 8601 or a simple "YYYY-MM-DD HH:MM" form.
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # Try ISO fromisoformat as a last resort (handles timezone offsets).
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


class RepairSpecialist(StatelessSpecialist):
    name = SpecialistName.REPAIR
    domain = "repair"

    def choose_capability(self, step, state, prior_results):
        del step, prior_results
        if state.slots.get("work_order_id"):
            return "repair_get"
        if state.slots.get("action") in {"create", "submit"}:
            return "repair_create"
        return "repair_list"

    def project_parameters(self, capability, step, state, prior_results):
        del prior_results
        values = {**state.slots, **step.parameters}
        if capability == "repair_get":
            return {"work_order_id": str(values.get("work_order_id") or "")}
        if capability == "repair_create":
            params = {
                "description": str(values.get("description") or ""),
                "location": str(values.get("location") or ""),
                "urgency": str(values.get("urgency") or "NORMAL"),
            }
            # appointment_at 是必填槽位但允许“稍后协商”延期。用户尚未回答时故意不传该
            # 字段，使输入校验失败并驱动编排层追问；用户已回答（具体时间或“稍后协商”）
            # 时才传入（“稍后协商”解析为 None，表示延期预约）。
            if "appointment_at" in values:
                params["appointment_at"] = _parse_appointment_at(values.get("appointment_at"))
            return params
        return {
            "statuses": tuple(values.get("statuses") or ()),
            "limit": int(values.get("limit") or 20),
            "location": values.get("location"),
            "category": values.get("category"),
            "assigned_to_me": bool(values.get("assigned_to_me", False)),
        }
