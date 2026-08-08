"""确认动作节点 — PRD §6.5.7。

在调用任何业务写 Service **之前**构造待确认操作并 interrupt（写-低风险）。
写-高风险不在此确认，直接置 ``handover_required`` 转授权人工。
读操作不中断，交由后续节点直接执行。

恢复语义：
* 确认通过 —— 把平台下发的确认令牌写入 ``state.confirmation_token``，
  工具层据此才允许调用写 Service。
* 用户取消 —— 清空 ``pending_action``，路由据此跳过执行，
  不产生任何业务对象（PRD A-03）。
"""

from datetime import datetime, timezone

from property_agent.agent.graph_core import interrupt
from property_agent.agent.policies import OperationLevel, classify_operation_level


def _build_pending(state):
    return {
        "intent": state.intent,
        "tool": state.slots.get("tool"),
        "params": {
            k: v for k, v in state.slots.items() if k not in ("user_text", "tool")
        },
        # 确认有效期起点：应用重启后恢复前必须重新校验（PRD §6.5.8）
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }


def _summary(pending: dict) -> str:
    return f"确认执行 {pending.get('intent')} 操作：{pending.get('tool')}"


def confirm_action_node():
    def node(state):
        if state._resume is not None:
            if state._resume.get("confirmed"):
                token = state._resume.get("confirmation_token") or "agent-confirmed"
                state.confirmation_token = token
                state.add_message("assistant", "已确认，正在为您办理。")
            else:
                state.pending_action = None
                state.confirmation_token = None
                state.add_message("assistant", "已取消，未执行任何操作。")
            return state

        pending = _build_pending(state)
        state.pending_action = pending
        level = classify_operation_level(state.intent, pending.get("tool"))
        state.operation_level = level

        if level == OperationLevel.WRITE_HIGH_RISK.value:
            state.handover_required = True
            return state  # 转人工接管，不在此 interrupt

        if level == OperationLevel.WRITE_LOW_RISK.value:
            # 暂停等待用户确认；此刻尚未调用任何业务写 Service（PRD A-03）
            interrupt(
                {
                    "type": "confirmation",
                    "summary": _summary(pending),
                    "action": pending,
                }
            )
        return state

    return node
