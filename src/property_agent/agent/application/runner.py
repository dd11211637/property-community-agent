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

from property_agent.agent.application.accepted_head import (
    cursor_for,
    publish_accepted,
    result_from_payload,
    runtime_for,
)
from property_agent.agent.application.conversation_service import (
    AgentContext,
    ConversationService,
    ConversationSnapshot,
)
from property_agent.agent.application.errors import AgentSessionError, AgentSessionErrorCode
from property_agent.agent.application.graph_engine import (
    GraphEngine,
    GraphExecutionResult,
    LegacyGraphEngine,
)
from property_agent.agent.application.memory_outcome import accepted_turn_outcome
from property_agent.agent.application.pending_confirmation import (
    confirmation_envelope,
)
from property_agent.agent.application.recovery import AgentRecoveryService
from property_agent.agent.application.runner_signals import (
    first_turn_inspection_signal as _first_turn_inspection_signal,  # noqa: F401
)
from property_agent.agent.application.start_state import prepare_start_state
from property_agent.agent.application.turn_guard import TurnLeaseController
from property_agent.agent.graph_core import CompiledGraph
from property_agent.agent.infrastructure.checkpointer import CheckpointVersionConflict
from property_agent.agent.infrastructure.run_lease import Lease, LeaseHeartbeat, StaleAgentRunError
from property_agent.agent.observability import AgentObservability
from property_agent.agent.runtime_version import AgentRuntimeVersion
from property_agent.agent.state import GraphState

logger = getLogger(__name__)

ConfirmationTokenProvider = Callable[[GraphState], str]
TurnRecorder = Callable[[AgentContext, GraphState, str, str], None]
RuntimeRoute = Callable[[ConversationSnapshot | None], tuple[GraphEngine | None, str]]


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


@dataclass
class _TurnPlan:
    """一轮装配结果，供 ``start`` / ``stream_start``（以及 resume 变体）共用。"""

    lease: Lease | None
    ctx: AgentContext
    state: GraphState
    conversation: Any
    expected_version: int | None
    repair_followup_message: str | None
    represent_pending: bool = False
    engine: "GraphEngine | None" = None
    heartbeat: "LeaseHeartbeat | None" = None


class AgentSessionRunner:
    def __init__(
        self,
        *,
        graph: CompiledGraph,
        conversations: ConversationService,
        recovery: AgentRecoveryService,
        confirmation_token_provider: ConfirmationTokenProvider | None = None,
        turn_recorder: TurnRecorder | None = None,
        memory_writer: Any | None = None,
        checkpointer: Any | None = None,
        run_lease: Any | None = None,
        approval_service: Any | None = None,
        enforce_concurrency: bool = True,
        observability: AgentObservability | None = None,
        heartbeat_interval_seconds: int = 10,
    ) -> None:
        self._graph = graph
        self._conversations = conversations
        self._recovery = recovery
        self._confirmation_token_provider = confirmation_token_provider
        self._turn_recorder = turn_recorder
        self._memory_writer = memory_writer
        self._checkpointer = checkpointer
        self._run_lease = run_lease
        self._approval_service = approval_service
        # P0 并发护栏总开关：关闭时不做 lease/CAS（兼容未升级库或回滚场景）。
        self._enforce = enforce_concurrency
        # P0-5 heartbeat：后台续期间隔（默认 10s，覆盖 30s lease TTL）。
        self._turn_guard = TurnLeaseController(
            lambda: self._run_lease,
            checkpointer,
            enforce=lambda: self._enforce,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        # P1 观测与流式：4 关键指标 + agent.turn root span（缺省进程内实现）。
        self._observability = observability or AgentObservability.in_memory()

    def start(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        user_text: str,
        house_id: UUID | None = None,
        slots: dict[str, Any] | None = None,
        engine: GraphEngine | None = None,
        runtime_version: str = AgentRuntimeVersion.V1.value,
        runtime_route: RuntimeRoute | None = None,
    ) -> AgentTurn:
        plan = None
        try:
            plan = self._plan_start(
                conversation_id=conversation_id,
                context=context,
                user_text=user_text,
                house_id=house_id,
                slots=slots,
                engine=engine,
                runtime_version=runtime_version,
                runtime_route=runtime_route,
            )
            if plan.represent_pending:
                return self._pending_turn(plan)
            if plan.repair_followup_message:
                return self._return_done(plan.ctx, plan.state, conversation_id, user_text)
            with self._observability.observe_turn(
                conversation_id=conversation_id,
                run_id=self._lease_run_id(plan),
                fence=self._lease_fence(plan),
                expected_version=plan.expected_version,
            ) as span:
                result = plan.engine.invoke(
                    plan.state, thread_id=conversation_id, runtime=runtime_for(plan)
                )
                self._turn_guard.assert_alive(plan.lease, plan.heartbeat)
                accepted_version = publish_accepted(
                    self._checkpointer, conversation_id, plan, result
                )
                turn = self._finalize(result)
                self._persist_accepted(plan, turn, user_text, accepted_version)
                span.set_attribute("agent.intent", turn.state.intent)
                span.set_attribute("agent.degraded", self._observability.degraded)
                return turn
        except AgentSessionError as exc:
            # P1 观测：同会话并发被拒 → conversation_busy 指标。
            if exc.code == AgentSessionErrorCode.CONVERSATION_BUSY:
                self._observability.metrics.conversation_busy.inc()
            raise
        except CheckpointVersionConflict:
            # P1 观测：checkpoint CAS 命中 stale 版本。
            self._observability.metrics.checkpoint_conflict.inc()
            raise
        except StaleAgentRunError:
            # P1 观测：旧 worker 凭旧 fence 的业务写被 fencing 拒绝。
            self._observability.metrics.stale_fence_rejected.inc()
            raise
        finally:
            if plan is not None:
                self._turn_guard.stop_heartbeat(plan.heartbeat)
                self._turn_guard.release(conversation_id, plan.lease)

    def stream_start(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        user_text: str,
        house_id: UUID | None = None,
        slots: dict[str, Any] | None = None,
        engine: GraphEngine | None = None,
        runtime_version: str = AgentRuntimeVersion.V1.value,
        runtime_route: RuntimeRoute | None = None,
    ):
        """真流式发起一轮（P1 观测与流式）。

        生成器：先 yield ``run_started``（graph 完成前即发出，降低 time-to-first-event），
        再按图节点生命周期 yield ``tool_started`` / ``tool_finished``，最后 yield
        ``("__turn__", AgentTurn)`` 由 API 层展开为 intent/message/confirmation/facts/done。
        """
        plan = None
        try:
            plan = self._plan_start(
                conversation_id=conversation_id,
                context=context,
                user_text=user_text,
                house_id=house_id,
                slots=slots,
                engine=engine,
                runtime_version=runtime_version,
                runtime_route=runtime_route,
            )
            if plan.represent_pending:
                yield ("run_started", {"conversation_id": conversation_id})
                yield ("__turn__", self._pending_turn(plan))
                return
            if plan.repair_followup_message:
                turn = self._return_done(plan.ctx, plan.state, conversation_id, user_text)
                yield ("run_started", {"conversation_id": conversation_id})
                yield ("__turn__", turn)
                return
            yield ("run_started", {"conversation_id": conversation_id})
            with self._observability.observe_turn(
                conversation_id=conversation_id,
                run_id=self._lease_run_id(plan),
                fence=self._lease_fence(plan),
                expected_version=plan.expected_version,
            ) as span:
                for kind, payload in plan.engine.invoke_stream(
                    plan.state,
                    thread_id=conversation_id,
                    runtime=runtime_for(plan),
                ):
                    if kind == "node_enter":
                        yield ("tool_started", {"node": payload["node"]})
                    elif kind == "node_exit":
                        yield ("tool_finished", {"node": payload["node"]})
                    elif kind == "__final__":
                        result = result_from_payload(payload)
                        self._turn_guard.assert_alive(plan.lease, plan.heartbeat)
                        accepted_version = publish_accepted(
                            self._checkpointer, conversation_id, plan, result
                        )
                        turn = self._finalize(result)
                        self._persist_accepted(plan, turn, user_text, accepted_version)
                        span.set_attribute("agent.intent", turn.state.intent)
                        span.set_attribute("agent.degraded", self._observability.degraded)
                        yield ("__turn__", turn)
        except AgentSessionError as exc:
            if exc.code == AgentSessionErrorCode.CONVERSATION_BUSY:
                self._observability.metrics.conversation_busy.inc()
            raise
        except CheckpointVersionConflict:
            self._observability.metrics.checkpoint_conflict.inc()
            raise
        except StaleAgentRunError:
            self._observability.metrics.stale_fence_rejected.inc()
            raise
        finally:
            if plan is not None:
                self._turn_guard.stop_heartbeat(plan.heartbeat)
                self._turn_guard.release(conversation_id, plan.lease)

    def _plan_start(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        user_text: str,
        house_id: UUID | None,
        slots: dict[str, Any] | None,
        engine: GraphEngine | None = None,
        runtime_version: str = AgentRuntimeVersion.V1.value,
        runtime_route: RuntimeRoute | None = None,
    ) -> "_TurnPlan":
        # P0-2: lease 必须在 turn 最外层获取——任何会修改 conversation 状态的
        # 操作（ConversationService.start、recovery.peek、confirmation_token_provider、
        # graph.invoke、sync_from_state）都必须处于 lease ownership 下。
        lease = self._turn_guard.acquire(conversation_id, uuid4())
        ctx = self._turn_guard.activate(context, lease)
        if runtime_route is not None:
            engine, runtime_version = runtime_route(self._conversations.get(conversation_id))
        # PR4 §8：runtime 版本在创建时钉死；已存在会话沿用持久化值，绝不切换。
        conversation = self._conversations.start(
            conversation_id=conversation_id,
            context=ctx,
            current_house_id=house_id,
            runtime_version=runtime_version,
        )
        current_house_id = house_id or conversation.current_house_id
        previous = self._recovery.peek(conversation_id)
        represent_pending = bool(
            conversation.is_v2 and previous is not None and previous.pending_action is not None
        )
        if represent_pending:
            return _TurnPlan(
                lease=lease,
                ctx=ctx,
                state=previous,
                conversation=conversation,
                expected_version=self._turn_guard.version(conversation_id),
                repair_followup_message=None,
                represent_pending=True,
                engine=engine or LegacyGraphEngine(self._graph),
            )
        prepared = prepare_start_state(
            conversation_id=conversation_id,
            context=ctx,
            current_house_id=current_house_id,
            previous=previous,
            user_text=user_text,
            slots=slots,
        )
        # P0-3: expected_version 必须在成功 acquire lease 后读取，避免读到
        # 被并发新 run 覆盖的版本导致不必要 stale CAS。
        expected_version = self._turn_guard.version(conversation_id)
        # P0-5: heartbeat——graph.invoke 前先单次续期确认 lease 仍有效，
        # 成功后再启动后台周期续租（失败则不会启动后台线程，避免泄漏）。
        self._turn_guard.renew(lease)
        heartbeat = self._turn_guard.start_heartbeat(lease)
        return _TurnPlan(
            lease=lease,
            ctx=ctx,
            state=prepared.state,
            conversation=conversation,
            expected_version=expected_version,
            repair_followup_message=prepared.repair_followup_message,
            engine=engine or LegacyGraphEngine(self._graph),
            heartbeat=heartbeat,
        )

    @staticmethod
    def _pending_turn(plan: "_TurnPlan") -> AgentTurn:
        return AgentTurn(
            state=plan.state,
            conversation=plan.conversation,
            interrupt=confirmation_envelope(plan.state),
            done=False,
        )

    def _return_done(
        self, context: AgentContext, state: GraphState, conversation_id: str, user_text: str
    ) -> AgentTurn:
        result = GraphExecutionResult(state=state, interrupt=None, done=True)
        turn = self._finalize(result)
        self._persist_turn(context, turn, user_text)
        return turn

    @staticmethod
    def _lease_run_id(plan: "_TurnPlan") -> Any | None:
        """lease 可能为 None（_enforce=False 的测试/退化路径），安全取 run_id。"""
        return plan.lease.run_id if plan.lease is not None else None

    @staticmethod
    def _lease_fence(plan: "_TurnPlan") -> int | None:
        """lease 可能为 None（_enforce=False 的测试/退化路径），安全取 fence。"""
        return plan.lease.fence if plan.lease is not None else None

    def resume(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        confirmed: bool,
        confirmation_token: str | None = None,
        action_hash: str | None = None,
        engine: GraphEngine | None = None,
    ) -> AgentTurn:
        plan = None
        token = confirmation_token
        try:
            plan, token = self._plan_resume(
                conversation_id=conversation_id,
                context=context,
                confirmed=confirmed,
                confirmation_token=confirmation_token,
                action_hash=action_hash,
                engine=engine,
            )
            with self._observability.observe_turn(
                conversation_id=conversation_id,
                run_id=self._lease_run_id(plan),
                fence=self._lease_fence(plan),
                expected_version=plan.expected_version,
                confirmed=confirmed,
            ) as span:
                result = plan.engine.resume(
                    conversation_id,
                    {"confirmed": confirmed, "confirmation_token": token},
                    state=plan.state,
                    runtime=runtime_for(plan, token=token if confirmed else None),
                    runtime_cursor=cursor_for(self._checkpointer, conversation_id),
                )
                self._turn_guard.assert_alive(plan.lease, plan.heartbeat)
                accepted_version = publish_accepted(
                    self._checkpointer, conversation_id, plan, result
                )
                turn = self._finalize(result)
                action_text = "确认执行操作" if confirmed else "取消待确认操作"
                self._persist_accepted(
                    plan, turn, action_text, accepted_version, cancelled=not confirmed
                )
                span.set_attribute("agent.intent", turn.state.intent)
                span.set_attribute("agent.degraded", self._observability.degraded)
                return turn
        except AgentSessionError as exc:
            if exc.code == AgentSessionErrorCode.CONVERSATION_BUSY:
                self._observability.metrics.conversation_busy.inc()
            raise
        except CheckpointVersionConflict:
            self._observability.metrics.checkpoint_conflict.inc()
            raise
        except StaleAgentRunError:
            self._observability.metrics.stale_fence_rejected.inc()
            raise
        except Exception:
            # P1 观测：已确认（审批已被业务 UoW 消费）却因后续业务失败整体回滚。
            if confirmed:
                self._observability.metrics.approval_rollback.inc()
            raise
        finally:
            if plan is not None:
                self._turn_guard.stop_heartbeat(plan.heartbeat)
                self._turn_guard.release(conversation_id, plan.lease)

    def stream_resume(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        confirmed: bool,
        confirmation_token: str | None = None,
        action_hash: str | None = None,
        engine: GraphEngine | None = None,
    ):
        """真流式恢复一轮（P1 观测与流式）。语义同 ``stream_start``。"""
        plan = None
        token = confirmation_token
        try:
            plan, token = self._plan_resume(
                conversation_id=conversation_id,
                context=context,
                confirmed=confirmed,
                confirmation_token=confirmation_token,
                action_hash=action_hash,
                engine=engine,
            )
            yield (
                "run_started",
                {"conversation_id": conversation_id, "confirmed": confirmed},
            )
            with self._observability.observe_turn(
                conversation_id=conversation_id,
                run_id=self._lease_run_id(plan),
                fence=self._lease_fence(plan),
                expected_version=plan.expected_version,
                confirmed=confirmed,
            ) as span:
                for kind, payload in plan.engine.resume_stream(
                    conversation_id,
                    {"confirmed": confirmed, "confirmation_token": token},
                    state=plan.state,
                    runtime=runtime_for(plan, token=token if confirmed else None),
                    runtime_cursor=cursor_for(self._checkpointer, conversation_id),
                ):
                    if kind == "node_enter":
                        yield ("tool_started", {"node": payload["node"]})
                    elif kind == "node_exit":
                        yield ("tool_finished", {"node": payload["node"]})
                    elif kind == "__final__":
                        result = result_from_payload(payload)
                        self._turn_guard.assert_alive(plan.lease, plan.heartbeat)
                        accepted_version = publish_accepted(
                            self._checkpointer, conversation_id, plan, result
                        )
                        turn = self._finalize(result)
                        action_text = "确认执行操作" if confirmed else "取消待确认操作"
                        self._persist_accepted(
                            plan, turn, action_text, accepted_version, cancelled=not confirmed
                        )
                        span.set_attribute("agent.intent", turn.state.intent)
                        span.set_attribute("agent.degraded", self._observability.degraded)
                        yield ("__turn__", turn)
                return
        except AgentSessionError as exc:
            if exc.code == AgentSessionErrorCode.CONVERSATION_BUSY:
                self._observability.metrics.conversation_busy.inc()
            raise
        except CheckpointVersionConflict:
            self._observability.metrics.checkpoint_conflict.inc()
            raise
        except StaleAgentRunError:
            self._observability.metrics.stale_fence_rejected.inc()
            raise
        except Exception:
            if confirmed:
                self._observability.metrics.approval_rollback.inc()
            raise
        finally:
            if plan is not None:
                self._turn_guard.stop_heartbeat(plan.heartbeat)
                self._turn_guard.release(conversation_id, plan.lease)

    def _plan_resume(
        self,
        *,
        conversation_id: str,
        context: AgentContext,
        confirmed: bool,
        confirmation_token: str | None,
        action_hash: str | None,
        engine: GraphEngine | None = None,
    ) -> tuple["_TurnPlan", str | None]:
        # P0-2: lease 必须在最外层——restore、confirmation_token_provider（写
        # token + create_pending + approve）、graph.resume 都会修改 conversation
        # 状态，必须处于 lease ownership 下。
        lease = self._turn_guard.acquire(conversation_id, uuid4())
        ctx = self._turn_guard.activate(context, lease)
        restored = self._recovery.restore(conversation_id, ctx, expected_action_hash=action_hash)
        # HTTP 层不接受客户端令牌。生产装配存在 provider 时始终覆盖调用方值，
        # 令牌只能由服务端根据恢复后的可信待确认参数签发。
        if confirmed and self._confirmation_token_provider is not None:
            confirmation_token = self._confirmation_token_provider(restored.state)
        if confirmed and not confirmation_token:
            raise RuntimeError("confirmation token provider is not configured")
        # P0-3: expected_version 在 acquire lease 后读取。
        expected_version = self._turn_guard.version(conversation_id)
        # P0-5: heartbeat——graph.resume 前先单次续期确认 lease 仍有效，
        # 成功后再启动后台周期续租（失败则不会启动后台线程，避免泄漏）。
        self._turn_guard.renew(lease)
        heartbeat = self._turn_guard.start_heartbeat(lease)
        plan = _TurnPlan(
            lease=lease,
            ctx=ctx,
            state=restored.state,
            conversation=None,
            expected_version=expected_version,
            repair_followup_message=None,
            engine=engine or LegacyGraphEngine(self._graph),
            heartbeat=heartbeat,
        )
        return plan, confirmation_token

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
        lease = self._turn_guard.acquire(conversation_id, uuid4())
        try:
            self._conversations.require_owned_by(conversation_id, context)
            return self._conversations.close(conversation_id)
        finally:
            self._turn_guard.release(conversation_id, lease)

    # ---- 内部 ----

    def _finalize(self, result: GraphExecutionResult | dict[str, Any]) -> AgentTurn:
        if isinstance(result, GraphExecutionResult):
            state = result.state
            done = bool(result.done)
            interrupt = result.interrupt
        else:
            state = result["state"]
            done = bool(result.get("done", True))
            interrupt = result.get("interrupt")
        conversation = self._conversations.sync_from_state(state, waiting_confirm=not done)
        return AgentTurn(
            state=state,
            conversation=conversation,
            interrupt=interrupt,
            done=done,
        )

    def _persist_turn(
        self,
        context: AgentContext,
        turn: AgentTurn,
        user_text: str,
        *,
        accepted_version: int | None = None,
        outcome: Any | None = None,
    ) -> None:
        from property_agent.agent.application.transcript import record_turn

        record_turn(self._turn_recorder, context, turn, user_text)
        if self._memory_writer is not None and accepted_version is not None:
            try:
                self._memory_writer.write_accepted_turn(
                    context=context,
                    state=turn.state,
                    user_text=user_text,
                    assistant_text=turn.reply,
                    accepted_version=accepted_version,
                    outcome=outcome,
                )
            except Exception:
                logger.exception(
                    "memory_writer_degraded",
                    extra={"conversation_id": turn.state.conversation_id},
                )

    def _persist_accepted(
        self,
        plan: _TurnPlan,
        turn: AgentTurn,
        user_text: str,
        accepted_version: int | None,
        *,
        cancelled: bool = False,
    ) -> None:
        self._persist_turn(
            plan.ctx,
            turn,
            user_text,
            accepted_version=accepted_version,
            outcome=accepted_turn_outcome(turn.state, done=turn.done, cancelled=cancelled),
        )
