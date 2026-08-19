"""联机 harness：驱动真实后端 API 现场运行 Agent。

与 RecordedHarness 的区别：这里没有预录数据——评测启动时登录真实账号、
向真实 /api/agent/conversations 发送用例输入，轨迹由真实服务当场产生。
转录只读取响应，不修改任何状态。

依赖：
- 后端已通过 compose 启动（postgres + migrate + seed + backend）。
- 账号映射：用例 input.context.username 显式指定；否则按 role 取默认账号。
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import httpx

from judge.harness.base import AgentHarnessPort
from judge.schemas import AgentRun, CaseInput, TraceEvent

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_PASSWORD = "123456"

ROLE_ACCOUNTS: dict[str, str] = {
    "resident": "zhangsan",
    "staff": "customer_service",
    "security": "security_guard",
    "finance": "finance",
    "manager": "manager",
    "admin": "manager",
}


class LiveHarness:
    """通过公开 HTTP API 驱动真实 Agent，并转录为可评估运行。"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30)
        self._tokens: dict[str, str] = {}
        self._houses: dict[str, str | None] = {}

    def close(self) -> None:
        self._client.close()

    # ── 公开接口 ──────────────────────────────────────────────

    def run(self, case_id: str, case_input: CaseInput) -> AgentRun:
        token = self._login(case_input)
        conversation_id = f"judge-{case_id}-{uuid4().hex[:8]}"
        events: list[TraceEvent] = []
        final_reply = ""
        handover = False
        auto_confirm = bool(case_input.context.get("auto_confirm", True))
        for turn_text in case_input.turns:
            payload = self._send(conversation_id, token, turn_text, case_input)
            events, final_reply = _merge_turn(events, payload)
            handover = handover or bool(payload.get("handover_required"))
            payload, events, drain_reply = self._drain_confirmations(
                conversation_id, token, payload, events, auto_confirm
            )
            if drain_reply:
                final_reply = drain_reply
            handover = handover or bool(payload.get("handover_required"))
        return AgentRun(
            case_id=case_id,
            agent_mode="deepseek" if os.environ.get("DEEPSEEK_API_KEY") else "keyword",
            events=events,
            final_answer=final_reply,
            handover_required=handover,
            degraded=bool(payload.get("degraded", False)),
        )

    # ── 内部 ─────────────────────────────────────────────────

    def _login(self, case_input: CaseInput) -> str:
        username = str(
            case_input.context.get("username") or ROLE_ACCOUNTS.get(case_input.role, "zhangsan")
        )
        if username in self._tokens:
            return self._tokens[username]
        response = self._client.post(
            "/api/auth/login",
            json={"username": username, "password": DEFAULT_PASSWORD},
        )
        response.raise_for_status()
        body = response.json()
        self._tokens[username] = body["access_token"]
        # 平台要求会话消息显式携带 house_id（多房屋账号需先选房）
        self._houses[username] = body.get("current_house_id")
        return self._tokens[username]

    def _house_of(self, case_input: CaseInput, token: str) -> str | None:
        if case_input.house_id:
            return case_input.house_id
        for username, saved in self._houses.items():
            if self._tokens.get(username) == token and saved:
                return saved
        return None

    def _drain_confirmations(
        self,
        conversation_id: str,
        token: str,
        payload: dict[str, Any],
        events: list[TraceEvent],
        auto_confirm: bool,
    ) -> tuple[dict[str, Any], list[TraceEvent], str]:
        """写闭环：收到确认卡后自动回确认，转录确认与真实工具执行。

        auto_confirm=False 时保留中断现场（考察"未确认不执行"的用例用它）。
        """
        final_reply = ""
        for _ in range(3):
            card = payload.get("pending_confirmation")
            if not card or not auto_confirm:
                break
            response = self._client.post(
                f"/api/agent/conversations/{conversation_id}/confirmations",
                headers={"Authorization": f"Bearer {token}"},
                json={"confirmed": True, "action_hash": card.get("action_hash")},
            )
            response.raise_for_status()
            body = response.json()
            payload = body.get("data") or {}
            if not body.get("success", False):
                raise RuntimeError(f"确认执行失败: {body}")
            events.append(
                TraceEvent(
                    step=len(events) + 1,
                    type="confirmation_granted",
                    name=str(card.get("tool") or ""),
                )
            )
            events = _emit_write_execution(events, str(card.get("tool") or ""), payload)
            events, reply = _merge_turn(events, payload, skip_intent=True)
            final_reply = reply or final_reply
        return payload, events, final_reply

    def _send(
        self,
        conversation_id: str,
        token: str,
        text: str,
        case_input: CaseInput,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text}
        house_id = case_input.house_id or self._house_of(case_input, token)
        if house_id:
            body["house_id"] = house_id
        slots = case_input.context.get("slots")
        if slots:
            body["slots"] = slots
        response = self._client.post(
            f"/api/agent/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        if not payload.get("success", False):
            raise RuntimeError(f"Agent 会话失败: {payload}")
        return data


def _emit_write_execution(
    events: list[TraceEvent], tool: str, payload: dict[str, Any]
) -> list[TraceEvent]:
    """确认后的真实写执行：turn_data 的 facts/error 即工具结果。"""
    facts = payload.get("facts")
    error = payload.get("error")
    ok = facts is not None and not error
    events.append(TraceEvent(step=len(events) + 1, type="tool_call", name=tool))
    events.append(
        TraceEvent(
            step=len(events) + 1,
            type="tool_result",
            name=tool,
            ok=ok,
            params={"facts": facts or {}, "error": str(error or "")},
        )
    )
    return events


def _merge_turn(
    events: list[TraceEvent],
    payload: dict[str, Any],
    *,
    skip_intent: bool = False,
) -> tuple[list[TraceEvent], str]:
    """把一次真实 turn 响应转录为轨迹事件，多轮时追加并重排序号。"""

    def emit(event_type: str, name: str = "", **kwargs: Any) -> None:
        events.append(TraceEvent(step=len(events) + 1, type=event_type, name=name, **kwargs))

    intent = payload.get("intent")
    if intent and not skip_intent:
        emit("intent", name=str(intent))
    if payload.get("requested_slot"):
        emit("slot_request", name=str(payload["requested_slot"]))
    for slot in payload.get("missing_slots") or []:
        emit("slot_request", name=str(slot), detail="缺少必填槽位，等待用户补充")
    trace = payload.get("agent_trace") or {}
    for entry in trace.get("events", []):
        entry_type = entry.get("type")
        if entry_type == "tool_call":
            emit("tool_call", name=str(entry.get("tool", "")))
        elif entry_type == "observation":
            emit(
                "tool_result",
                name=str(entry.get("tool", "")),
                ok=bool(entry.get("ok", False)),
                params={
                    "record_count": entry.get("record_count"),
                    "error_code": entry.get("error_code"),
                },
            )
    if payload.get("pending_confirmation"):
        emit("confirmation_request", detail="服务端下发确认卡片，等待用户确认")
    if payload.get("handover_required"):
        emit("handover", detail="high risk operation")
    reply = str(payload.get("reply") or "")
    if reply:
        emit("reply", detail=reply)
    return events, reply or (str(payload.get("slot_prompt") or ""))


def _deepseek_enabled() -> bool:
    """网关形态不进响应；按环境是否存在 API Key 判定（无 Key 必然降级）。"""
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


__all__ = ["AgentHarnessPort", "LiveHarness", "ROLE_ACCOUNTS", "DEFAULT_BASE_URL"]
