"""会话运行时 — 把主图、Conversation 业务表与恢复守卫串起来（PRD §6.5.8）。

一轮对话的完整链路：

    start   : Conversation.start → 注入可信身份 → graph.invoke → 同步业务表
    resume  : 恢复守卫三项校验   → graph.resume(state=校验后的快照) → 同步业务表

关键约束：

* ``thread_id`` 恒等于稳定的 ``conversation_id``；
* actor / community / house 只从可信上下文注入，用户自述一律忽略；
* resume 之前必须过恢复守卫，不允许直接调 ``graph.resume``；
* 中断挂起时会话进入 ``WAITING_CONFIRM``，转人工时进入 ``HANDOVER``。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from property_agent.agent.announcement_actions import resolve_announcement_followup
from property_agent.agent.announcement_time import (
    resolve_announcement_time_slots,
    trusted_business_date,
)
from property_agent.agent.application.conversation_service import (
    AgentContext,
    ConversationService,
    ConversationSnapshot,
)
from property_agent.agent.application.recovery import AgentRecoveryService
from property_agent.agent.graph_core import CompiledGraph
from property_agent.agent.policies import Intent
from property_agent.agent.state import GraphState

ConfirmationTokenProvider = Callable[[GraphState], str]
TurnRecorder = Callable[[AgentContext, GraphState, str, str], None]

_CONTEXTUAL_MARKERS = (
    "那",
    "那么",
    "刚才",
    "刚刚",
    "上个月",
    "上上个月",
    "本月",
    "这个月",
    "不是",
    "改成",
    "采用",
    "保存草稿",
    "立即发布",
    "确认发布",
    "定时发布",
    "预约发布",
    "换成",
    "那个",
    "这个",
    "重试",
    "再试",
)

_INSPECTION_ACTION_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("report_event", ("上报事件", "报告事件", "安防事件上报")),
    ("submit_disposal", ("提交处置", "处置结果", "完成处置")),
    ("start_task", ("开始巡检", "开始任务", "执行巡检")),
    ("add_record", ("追加记录", "添加记录", "补充记录")),
    ("submit_records", ("提交记录", "结束巡检")),
    ("create", ("创建巡检", "新建巡检", "安排巡检", "开展巡检", "进行巡检")),
    ("query", ("查询任务", "查询事件", "巡检任务", "巡检记录", "都完成", "完成了吗")),
)

_INSPECTION_SLOT_GROUPS = {
    "task_query": {"statuses", "assigned_to_me", "limit", "task_id"},
    "task_write": {
        "task_id",
        "expected_version",
        "title",
        "description",
        "point",
        "route_points",
        "note",
        "record_type",
    },
    "event_query": {"event_id", "statuses", "risk_levels", "assigned_to_me", "limit"},
    "event_write": {
        "event_id",
        "expected_version",
        "event_type",
        "risk_level",
        "location",
        "description",
        "note",
        "task_id",
    },
}


def _explicit_inspection_action(text: str) -> str | None:
    if any(marker in text for marker in ("了吗", "了没", "进度", "查询", "查看")):
        if any(marker in text for marker in ("巡检", "任务", "事件")):
            return "query"
    for action, markers in _INSPECTION_ACTION_MARKERS:
        if any(marker in text for marker in markers):
            if (
                action == "create"
                and "进行巡检" in text
                and "对" not in text
                and "我要" not in text
            ):
                continue
            return action
    return None


_FIRST_TURN_INSPECTION_MARKERS = ("巡检", "消防通道", "安防", "巡检发现", "检查发现")

_INSPECTION_WRITE_MARKERS = ("上报异常", "上报事件", "上报", "报告异常", "异常上报")


def _first_turn_inspection_signal(user_text: str, roles: tuple[str, ...]) -> dict[str, str]:
    """首轮确定性巡检信号：命中则锁定 INSPECTION 并走安防事件上报。

    仅靠 LLM 分类会把"消防通道堵塞上报"这类公共区域安防问题误判成住户报修；
    这里对巡检/安防写信号做确定性兜底（安全优先于模型判断）。``roles`` 只取
    可信请求上下文中的角色，不允许来自模型输出。
    """
    text = str(user_text or "")
    if any(marker in text for marker in _FIRST_TURN_INSPECTION_MARKERS) and any(
        marker in text for marker in _INSPECTION_WRITE_MARKERS
    ):
        return {"action": "report_event"}
    if "SECURITY_GUARD" in roles and any(
        marker in text for marker in ("堵塞", "异常", "可疑", "上报")
    ):
        return {"action": "report_event"}
    return {}


def _inspection_group(action: str, text: str) -> str:
    if action == "query":
        return "event_query" if "事件" in text or "安防" in text else "task_query"
    if action in {"report_event", "submit_disposal"}:
        return "event_write"
    return "task_write"


def _looks_contextual(text: str) -> bool:
    compact = text.strip()
    return bool(compact) and (
        len(compact) <= 24 and any(marker in compact for marker in _CONTEXTUAL_MARKERS)
    )


def _explicit_repair_corrections(text: str) -> dict[str, str]:
    """Extract user-authored corrections without asking a model to mutate trusted state."""
    if not any(marker in text for marker in ("不是", "改成", "换成")):
        return {}
    corrections: dict[str, str] = {}
    locations = ("厨房", "卫生间", "客厅", "卧室", "阳台", "玄关", "楼道", "车库")
    mentioned_locations = [value for value in locations if value in text]
    if mentioned_locations:
        corrections["location"] = mentioned_locations[-1]
    symptom_cues = (
        "漏电",
        "电路",
        "电线",
        "插座",
        "停电",
        "跳闸",
        "灯",
        "照明",
        "开关",
        "漏水",
        "水管",
        "下水",
        "水龙头",
        "马桶",
        "堵塞",
        "电梯",
        "困梯",
    )
    if any(cue in text for cue in symptom_cues):
        corrections["description"] = text.strip()
    return corrections


def _explicit_inspection_corrections(text: str, previous: GraphState | None) -> dict[str, str]:
    """Map a user correction to the active inspection field, never to identity fields."""
    if previous is None or previous.intent != "INSPECTION":
        return {}
    if not any(marker in text for marker in ("不是", "改成", "换成")):
        return {}
    locations = (
        "小区出入口",
        "楼栋大厅",
        "大厅",
        "消防通道",
        "地下车库",
        "车库",
        "公共设备间",
        "设备间",
    )
    mentioned = [value for value in locations if value in text]
    if not mentioned:
        return {}
    value = max(mentioned, key=len)
    action = str(previous.slots.get("action") or "")
    field = "location" if action in {"report_event", "create_event", "event_create"} else "point"
    return {field: value}


@dataclass(frozen=True)
class AgentTurn:
    """一轮执行结果。"""

    state: GraphState
    conversation: ConversationSnapshot
    interrupt: Any | None
    done: bool

    @property
    def awaiting_confirmation(self) -> bool:
        return not self.done and self.interrupt is not None

    @property
    def reply(self) -> str:
        for message in reversed(self.state.messages):
            if message.get("role") == "assistant":
                return str(message.get("content", ""))
        return ""


@dataclass(slots=True)
class _ContinuationState:
    previous_slots: dict[str, Any]
    previous_messages: list[dict[str, Any]]
    previous_intent: str | None
    single_slot_reply: dict[str, Any]
    slot_continuation: bool
    contextual_followup: bool
    continuing: bool


class AgentSessionRunner:
    def __init__(
        self,
        *,
        graph: CompiledGraph,
        conversations: ConversationService,
        recovery: AgentRecoveryService,
        confirmation_token_provider: ConfirmationTokenProvider | None = None,
        turn_recorder: TurnRecorder | None = None,
    ) -> None:
        self._graph = graph
        self._conversations = conversations
        self._recovery = recovery
        self._confirmation_token_provider = confirmation_token_provider
        self._turn_recorder = turn_recorder

    def start(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        user_text: str,
        house_id: UUID | None = None,
        slots: dict[str, Any] | None = None,
    ) -> AgentTurn:
        conversation = self._conversations.start(
            conversation_id=conversation_id,
            context=context,
            current_house_id=house_id,
        )
        current_house_id = house_id or conversation.current_house_id
        previous = self._recovery.peek(conversation_id)
        explicit_corrections = (
            _explicit_repair_corrections(user_text)
            if previous is not None and previous.intent == "REPAIR"
            else {}
        )
        explicit_corrections.update(_explicit_inspection_corrections(user_text, previous))
        roles = tuple(str(role) for role in getattr(context, "roles", ()))
        inspection_override = _first_turn_inspection_signal(user_text, roles)
        if inspection_override:
            explicit_corrections.update(inspection_override)
        inspection_action = _explicit_inspection_action(user_text)
        has_active_draft = bool(
            previous is not None
            and previous.intent == "ANNOUNCEMENT"
            and all(previous.slots.get(key) is not None for key in ("title", "body", "audience"))
        )
        active_draft = (
            {key: previous.slots[key] for key in ("title", "body", "audience")}
            if previous is not None and has_active_draft
            else None
        )
        announcement_followup = resolve_announcement_followup(
            user_text, has_active_draft=has_active_draft
        )
        announcement_action = (
            announcement_followup.action.value if announcement_followup.action else None
        )
        continuation = self._build_continuation(
            previous=previous,
            current_house_id=current_house_id,
            user_text=user_text,
            explicit_corrections=explicit_corrections,
        )
        self._apply_inspection_followup(continuation, previous, user_text, inspection_action)
        self._apply_announcement_followup(
            continuation,
            previous,
            user_text,
            announcement_action,
            announcement_followup,
        )
        # 已有活跃报修工单 + 用户改口/回归：不再重复建单，直接说明已有工单。
        repair_followup: dict[str, Any] = {}
        repair_followup_message: str | None = None
        repair_created_before = previous is not None and bool(
            previous.slots.get("work_order_id")
        )
        correction_or_return = any(
            marker in user_text for marker in ("不是", "改成", "换成", "回到", "刚才")
        )
        if repair_created_before and correction_or_return:
            business_no = str(previous.slots["work_order_id"])
            location = explicit_corrections.get("location") or previous.slots.get("location") or ""
            description = explicit_corrections.get("description") or previous.slots.get(
                "description"
            ) or ""
            repair_followup = {
                "work_order_id": business_no,
                "location": location,
                "description": description,
            }
            repair_followup_message = (
                f"您的报修工单 {business_no} 已提交（位置：{location}），"
                "正在处理中；如需正式修改地点或补充说明，请致电物业。"
            )
        state = GraphState(
            conversation_id=conversation_id,
            actor_id=context.actor_id,
            community_id=context.community_id,
            current_house_id=current_house_id,
            intent=(
                Intent.INSPECTION.value if inspection_override else continuation.previous_intent
            ),
            slots={
                **continuation.previous_slots,
                **explicit_corrections,
                **continuation.single_slot_reply,
                "roles": list(roles),
                "_user_corrected_fields": sorted(
                    set(explicit_corrections)
                    | set((announcement_followup.slot_updates or {}).keys())
                ),
                "_active_announcement_draft": active_draft,
                "user_text": user_text,
                **repair_followup,
                **(slots or {}),
            },
            messages=continuation.previous_messages,
            _continuation=continuation.continuing,
            _contextual_followup=continuation.contextual_followup,
        )
        state.add_message("user", user_text)
        if repair_followup_message:
            state.add_message("assistant", repair_followup_message)
            result = {
                "state": state,
                "interrupt": None,
                "thread_id": conversation_id,
                "done": True,
            }
            turn = self._finalize(result)
            self._persist_turn(context, turn, user_text)
            return turn
        result = self._graph.invoke(state, thread_id=conversation_id)
        turn = self._finalize(result)
        self._persist_turn(context, turn, user_text)
        return turn

    @staticmethod
    def _build_continuation(
        *,
        previous: GraphState | None,
        current_house_id: UUID | None,
        user_text: str,
        explicit_corrections: dict[str, str],
    ) -> _ContinuationState:
        same_house = previous is not None and previous.current_house_id == current_house_id
        slot_continuation = bool(
            same_house and previous.missing_slots and previous.pending_action is None
        )
        failed_turn_retry = bool(
            same_house
            and previous.error
            and any(marker in user_text for marker in ("重试", "再试"))
        )
        contextual_followup = bool(
            same_house
            and (previous.pending_action is None or explicit_corrections)
            and previous.intent
            and _looks_contextual(user_text)
        )
        continuing = slot_continuation or contextual_followup or failed_turn_retry
        previous_messages = list(previous.messages[-12:]) if same_house else []
        previous_slots: dict[str, Any] = {}
        previous_intent = None
        single_slot_reply: dict[str, Any] = {}
        if continuing and previous is not None:
            previous_slots = {
                key: value
                for key, value in previous.slots.items()
                if key not in {"user_text", "tool"}
            }
            previous_intent = previous.intent
            requested_slot = previous.requested_slot or (
                previous.missing_slots[0] if len(previous.missing_slots) == 1 else None
            )
            if slot_continuation and requested_slot and user_text.strip():
                single_slot_reply[requested_slot] = AgentSessionRunner._single_slot_value(
                    requested_slot, user_text
                )
        return _ContinuationState(
            previous_slots=previous_slots,
            previous_messages=previous_messages,
            previous_intent=previous_intent,
            single_slot_reply=single_slot_reply,
            slot_continuation=slot_continuation,
            contextual_followup=contextual_followup,
            continuing=continuing,
        )

    @staticmethod
    def _single_slot_value(requested_slot: str, user_text: str) -> Any:
        value: Any = user_text.strip()
        if requested_slot != "audience":
            return value
        import json

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {} if value == "全社区" else value

    @staticmethod
    def _apply_inspection_followup(
        continuation: _ContinuationState,
        previous: GraphState | None,
        user_text: str,
        action: str | None,
    ) -> None:
        if not action or previous is None:
            return
        group = _inspection_group(action, user_text)
        allowed = _INSPECTION_SLOT_GROUPS[group]
        continuation.previous_slots = {
            key: value for key, value in continuation.previous_slots.items() if key in allowed
        }
        continuation.previous_slots["action"] = action
        continuation.previous_slots["target"] = "event" if group.startswith("event") else "task"
        continuation.previous_intent = "INSPECTION"
        continuation.continuing = True

    @staticmethod
    def _apply_announcement_followup(
        continuation: _ContinuationState,
        previous: GraphState | None,
        user_text: str,
        action: str | None,
        followup: Any,
    ) -> None:
        if not action or previous is None or previous.intent != "ANNOUNCEMENT":
            return
        if action == "revise":
            previous.pending_action = None
            previous.confirmation_token = None
            previous._interrupt_node = None
        continuation.previous_slots = {
            key: value for key, value in previous.slots.items() if key not in {"user_text", "tool"}
        }
        continuation.previous_intent = "ANNOUNCEMENT"
        continuation.previous_slots["action"] = action
        business_date = trusted_business_date(previous.trusted_context.get("business_date"))
        continuation.previous_slots.update(
            resolve_announcement_time_slots(user_text, business_date)
        )
        continuation.previous_slots.update(followup.slot_updates or {})
        AgentSessionRunner._replace_optional_slot(
            continuation.previous_slots, "revision_instruction", followup.instruction
        )
        AgentSessionRunner._replace_optional_slot(
            continuation.previous_slots, "revision_detail_kind", followup.detail_kind
        )
        continuation.continuing = True
        continuation.contextual_followup = False

    @staticmethod
    def _replace_optional_slot(slots: dict[str, Any], key: str, value: Any) -> None:
        if value:
            slots[key] = value
        else:
            slots.pop(key, None)

    def resume(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        confirmed: bool,
        confirmation_token: str | None = None,
        action_hash: str | None = None,
    ) -> AgentTurn:
        restored = self._recovery.restore(
            conversation_id, context, expected_action_hash=action_hash
        )
        # HTTP 层不接受客户端令牌。生产装配存在 provider 时始终覆盖调用方值，
        # 令牌只能由服务端根据恢复后的可信待确认参数签发。
        if confirmed and self._confirmation_token_provider is not None:
            confirmation_token = self._confirmation_token_provider(restored.state)
        if confirmed and not confirmation_token:
            raise RuntimeError("confirmation token provider is not configured")
        result = self._graph.resume(
            conversation_id,
            {"confirmed": confirmed, "confirmation_token": confirmation_token},
            state=restored.state,
        )
        turn = self._finalize(result)
        action_text = "确认执行操作" if confirmed else "取消待确认操作"
        self._persist_turn(context, turn, action_text)
        return turn

    def status(
        self, *, conversation_id: str, context: AgentContext
    ) -> tuple[ConversationSnapshot, dict[str, Any] | None]:
        """查询会话当前状态与待确认操作（只读，不触发闸门副作用）。"""
        conversation = self._conversations.require_owned_by(conversation_id, context)
        state = self._recovery.peek(conversation_id)
        pending = None
        if state is not None and state._interrupt_node is not None:
            pending = state.pending_action
        return conversation, pending

    def close(self, *, conversation_id: str, context: AgentContext) -> ConversationSnapshot:
        self._conversations.require_owned_by(conversation_id, context)
        return self._conversations.close(conversation_id)

    # ---- 内部 ----

    def _finalize(self, result: dict[str, Any]) -> AgentTurn:
        state: GraphState = result["state"]
        done = bool(result["done"])
        conversation = self._conversations.sync_from_state(state, waiting_confirm=not done)
        return AgentTurn(
            state=state,
            conversation=conversation,
            interrupt=result.get("interrupt"),
            done=done,
        )

    def _persist_turn(self, context: AgentContext, turn: AgentTurn, user_text: str) -> None:
        from property_agent.agent.application.transcript import record_turn

        record_turn(self._turn_recorder, context, turn, user_text)
