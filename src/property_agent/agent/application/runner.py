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
from logging import getLogger
from typing import Any
from uuid import UUID, uuid4

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
from property_agent.agent.application.runner_signals import (
    ContinuationState as _ContinuationState,
)
from property_agent.agent.application.runner_signals import (
    build_initial_state as _build_initial_state,
)
from property_agent.agent.application.runner_signals import (
    explicit_inspection_action as _explicit_inspection_action,
)
from property_agent.agent.application.runner_signals import (
    explicit_inspection_corrections as _explicit_inspection_corrections,
)
from property_agent.agent.application.runner_signals import (
    explicit_repair_corrections as _explicit_repair_corrections,
)
from property_agent.agent.application.runner_signals import (
    first_turn_inspection_signal as _first_turn_inspection_signal,
)
from property_agent.agent.application.runner_signals import (
    inspection_group as _inspection_group,
)
from property_agent.agent.application.runner_signals import (
    looks_contextual as _looks_contextual,
)
from property_agent.agent.application.runner_signals import (
    resolve_repair_followup as _resolve_repair_followup,
)
from property_agent.agent.application.turn_guard import (
    acquire_turn_lease,
    activate_lease_context,
    heartbeat_turn_lease,
    read_turn_start_version,
    release_turn_lease,
)
from property_agent.agent.graph_core import CompiledGraph
from property_agent.agent.infrastructure.run_lease import Lease
from property_agent.agent.state import GraphState

logger = getLogger(__name__)

ConfirmationTokenProvider = Callable[[GraphState], str]
TurnRecorder = Callable[[AgentContext, GraphState, str, str], None]

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


class AgentSessionRunner:
    def __init__(
        self,
        *,
        graph: CompiledGraph,
        conversations: ConversationService,
        recovery: AgentRecoveryService,
        confirmation_token_provider: ConfirmationTokenProvider | None = None,
        turn_recorder: TurnRecorder | None = None,
        checkpointer: Any | None = None,
        run_lease: Any | None = None,
        approval_service: Any | None = None,
        enforce_concurrency: bool = True,
    ) -> None:
        self._graph = graph
        self._conversations = conversations
        self._recovery = recovery
        self._confirmation_token_provider = confirmation_token_provider
        self._turn_recorder = turn_recorder
        self._checkpointer = checkpointer
        self._run_lease = run_lease
        self._approval_service = approval_service
        # P0 并发护栏总开关：关闭时不做 lease/CAS（兼容未升级库或回滚场景）。
        self._enforce = enforce_concurrency

    def start(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        user_text: str,
        house_id: UUID | None = None,
        slots: dict[str, Any] | None = None,
    ) -> AgentTurn:
        # P0-2: lease 必须在 turn 最外层获取——任何会修改 conversation 状态的
        # 操作（ConversationService.start、recovery.peek、confirmation_token_provider、
        # graph.invoke、sync_from_state）都必须处于 lease ownership 下。
        lease = self._acquire_lease(conversation_id, uuid4())
        try:
            ctx = self._activate_lease_context(context, lease)
            conversation = self._conversations.start(
                conversation_id=conversation_id,
                context=ctx,
                current_house_id=house_id,
            )
            current_house_id = house_id or conversation.current_house_id
            previous = self._recovery.peek(conversation_id)
            explicit_corrections = self._collect_explicit_corrections(user_text, previous)
            roles = tuple(str(role) for role in getattr(ctx, "roles", ()))
            inspection_override = _first_turn_inspection_signal(user_text, roles)
            if inspection_override:
                explicit_corrections.update(inspection_override)
            inspection_action = _explicit_inspection_action(user_text)
            active_draft = self._active_announcement_draft(previous)
            has_active_draft = active_draft is not None
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
                continuation, previous, user_text, announcement_action, announcement_followup
            )
            repair_followup, repair_followup_message = _resolve_repair_followup(
                previous, user_text, explicit_corrections
            )
            state = _build_initial_state(
                conversation_id=conversation_id,
                context=ctx,
                current_house_id=current_house_id,
                user_text=user_text,
                slots=slots,
                inspection_override=inspection_override,
                explicit_corrections=explicit_corrections,
                continuation=continuation,
                roles=roles,
                active_draft=active_draft,
                announcement_followup=announcement_followup,
                repair_followup=repair_followup,
            )
            state.add_message("user", user_text)
            if repair_followup_message:
                state.add_message("assistant", repair_followup_message)
                return self._return_done(ctx, state, conversation_id, user_text)
            # P0-3: expected_version 必须在成功 acquire lease 后读取，避免读到
            # 被并发新 run 覆盖的版本导致不必要 stale CAS。
            expected_version = self._turn_start_version(conversation_id)
            # P0-5: heartbeat——graph.invoke 前续期一次，覆盖长 LLM turn。
            self._heartbeat(lease)
            result = self._graph.invoke(
                state, thread_id=conversation_id, expected_version=expected_version
            )
            turn = self._finalize(result)
            self._persist_turn(ctx, turn, user_text)
            return turn
        finally:
            self._release_lease(conversation_id, lease)

    def _collect_explicit_corrections(
        self, user_text: str, previous: GraphState | None
    ) -> dict[str, str]:
        corrections: dict[str, str] = (
            _explicit_repair_corrections(user_text)
            if previous is not None and previous.intent == "REPAIR"
            else {}
        )
        corrections.update(_explicit_inspection_corrections(user_text, previous))
        return corrections

    def _active_announcement_draft(self, previous: GraphState | None) -> dict[str, Any] | None:
        if previous is None or previous.intent != "ANNOUNCEMENT":
            return None
        if not all(previous.slots.get(key) is not None for key in ("title", "body", "audience")):
            return None
        return {key: previous.slots[key] for key in ("title", "body", "audience")}

    def _return_done(
        self, context: AgentContext, state: GraphState, conversation_id: str, user_text: str
    ) -> AgentTurn:
        result = {
            "state": state,
            "interrupt": None,
            "thread_id": conversation_id,
            "done": True,
        }
        turn = self._finalize(result)
        self._persist_turn(context, turn, user_text)
        return turn

    # ── P0 并发护栏（具体逻辑见 turn_guard.py） ────────────────────────

    def _acquire_lease(self, thread_id: str, run_id: UUID) -> Lease | None:
        return acquire_turn_lease(
            self._run_lease,
            enforce_concurrency=self._enforce,
            thread_id=thread_id,
            run_id=run_id,
        )

    def _heartbeat(self, lease: Lease | None) -> Lease | None:
        """续期 lease（P0-5）。失败抛 ``StaleAgentRunError``，由调用方终止 run。"""
        return heartbeat_turn_lease(self._run_lease, lease=lease)

    def _release_lease(self, thread_id: str, lease: Lease | None) -> None:
        if lease is None:
            return
        release_turn_lease(self._run_lease, thread_id=thread_id, run_id=lease.run_id)

    def _activate_lease_context(self, context: AgentContext, lease: Lease | None) -> AgentContext:
        """委托 turn_guard.activate_lease_context（P0-4 fencing 注入）。"""
        return activate_lease_context(context, lease)

    def _turn_start_version(self, thread_id: str) -> int | None:
        return read_turn_start_version(
            self._checkpointer, enforce_concurrency=self._enforce, thread_id=thread_id
        )

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
        # P0-2: lease 必须在最外层——restore、confirmation_token_provider（写
        # token + create_pending + approve）、graph.resume 都会修改 conversation
        # 状态，必须处于 lease ownership 下。
        lease = self._acquire_lease(conversation_id, uuid4())
        try:
            ctx = self._activate_lease_context(context, lease)
            restored = self._recovery.restore(
                conversation_id, ctx, expected_action_hash=action_hash
            )
            # HTTP 层不接受客户端令牌。生产装配存在 provider 时始终覆盖调用方值，
            # 令牌只能由服务端根据恢复后的可信待确认参数签发。
            if confirmed and self._confirmation_token_provider is not None:
                confirmation_token = self._confirmation_token_provider(restored.state)
            if confirmed and not confirmation_token:
                raise RuntimeError("confirmation token provider is not configured")
            # P0-3: expected_version 在 acquire lease 后读取。
            expected_version = self._turn_start_version(conversation_id)
            # P0-5: heartbeat——graph.resume 前续期一次。
            self._heartbeat(lease)
            result = self._graph.resume(
                conversation_id,
                {"confirmed": confirmed, "confirmation_token": confirmation_token},
                state=restored.state,
                expected_version=expected_version,
            )
            turn = self._finalize(result)
            action_text = "确认执行操作" if confirmed else "取消待确认操作"
            self._persist_turn(ctx, turn, action_text)
            return turn
        finally:
            self._release_lease(conversation_id, lease)

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
        # P0-2/P0-7: close 必须在 lease 内——防止与正在运行的 turn 竞争，
        # CLOSED conversation 不允许被旧 run 的 sync_from_state 恢复为 ACTIVE。
        lease = self._acquire_lease(conversation_id, uuid4())
        try:
            self._conversations.require_owned_by(conversation_id, context)
            return self._conversations.close(conversation_id)
        finally:
            self._release_lease(conversation_id, lease)

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
