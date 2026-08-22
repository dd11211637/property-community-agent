"""Graph State — PRD §6.5.4.

保存完成当前任务所需的会话状态。仅从可信 RequestContext 读取 actor / community /
current_house；不长期保存完整手机号、住户姓名与门牌组合、完整附件地址或无关账单数据。
切换房屋时由节点负责清除地址、工单和账单相关槽位。
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass
class GraphState:
    conversation_id: str
    actor_id: UUID | None = None
    community_id: UUID | None = None
    current_house_id: UUID | None = None
    intent: str | None = None
    confidence: float = 0.0
    slots: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    requested_slot: str | None = None
    operation_level: str | None = None  # read / write-low-risk / write-high-risk
    pending_action: dict[str, Any] | None = None
    confirmation_token: str | None = None
    # P0 审批原子化：服务端在确认时创建的 PENDING 审批引用，随确认回执下发，
    # 由工具层透传到业务 Service，在业务 UoW 内与 mutation 同事务消费（CONSUMED）。
    approval_ref: str | None = None
    tool_result: dict[str, Any] | None = None
    retry_count: int = 0
    handover_required: bool = False
    messages: list[dict[str, Any]] = field(default_factory=list)
    trusted_context: dict[str, Any] = field(default_factory=dict)
    read_facts: dict[str, Any] | None = None
    read_trace: dict[str, Any] | None = None
    error: str | None = None
    # 内部运行态（不进入业务语义）
    _resume: Any | None = None
    _interrupt_node: str | None = None
    _continuation: bool = False
    _contextual_followup: bool = False

    def add_message(self, role: str, content: str, **extra: Any) -> None:
        msg = {"role": role, "content": content, **extra}
        self.messages.append(msg)

    def to_dict(self) -> dict[str, Any]:
        def _enc(v: Any) -> Any:
            if isinstance(v, UUID):
                return str(v)
            return v

        return {
            "conversation_id": self.conversation_id,
            "actor_id": _enc(self.actor_id),
            "community_id": _enc(self.community_id),
            "current_house_id": _enc(self.current_house_id),
            "intent": self.intent,
            "confidence": self.confidence,
            "slots": self.slots,
            "missing_slots": self.missing_slots,
            "requested_slot": self.requested_slot,
            "operation_level": self.operation_level,
            "pending_action": self.pending_action,
            "confirmation_token": self.confirmation_token,
            "approval_ref": self.approval_ref,
            "tool_result": self.tool_result,
            "retry_count": self.retry_count,
            "handover_required": self.handover_required,
            "messages": self.messages,
            "trusted_context": self.trusted_context,
            "read_facts": self.read_facts,
            "read_trace": self.read_trace,
            "error": self.error,
            "_resume": self._resume,
            "_interrupt_node": self._interrupt_node,
            "_continuation": self._continuation,
            "_contextual_followup": self._contextual_followup,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphState":
        def _dec(k: str, v: Any) -> Any:
            if v is None:
                return None
            if k in ("actor_id", "community_id", "current_house_id"):
                return UUID(v)
            return v

        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: _dec(k, v) for k, v in data.items() if k in known})
