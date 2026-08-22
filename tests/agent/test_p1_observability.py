"""P1 观测与流式 —— observability 指标 / root span 单测 + runner 集成测试。

验证审查报告强调的 4 个关键指标在正确错误路径上被精确递增，以及
``agent.turn`` root span 不记录 PII、携带正确元数据。
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from property_agent.agent.application.errors import AgentSessionError, AgentSessionErrorCode
from property_agent.agent.graph_core import CompiledGraph, StateGraph
from property_agent.agent.infrastructure.checkpointer import CheckpointVersionConflict
from property_agent.agent.infrastructure.run_lease import StaleAgentRunError
from property_agent.agent.observability import (
    AgentObservability,
    InMemoryCounter,
    NullSpan,
)
from property_agent.config import settings
from tests.agent.test_agent_persistence import REPAIR_SLOTS, boot, ctx, session_factory

# ctx / session_factory 是 pytest 固件，由下方测试以同名参数注入；此处引用以免
# 被 ruff 误报未使用（F401），并避免 F811 重定义告警。
_ = (ctx, session_factory)


class FakeGraph:
    """最小图桩：在 invoke / resume 时按配置抛错，用于触发 runner 指标分支。"""

    def __init__(self, *, invoke_error=None, resume_error=None) -> None:
        self._invoke_error = invoke_error
        self._resume_error = resume_error

    def invoke(self, state, *, thread_id=None, expected_version=None):
        if self._invoke_error is not None:
            raise self._invoke_error
        raise AssertionError("FakeGraph.invoke 不应成功返回")

    def invoke_stream(self, state, *, thread_id=None, expected_version=None):
        if self._invoke_error is not None:
            raise self._invoke_error
        raise AssertionError("FakeGraph.invoke_stream 不应成功返回")

    def resume(self, thread_id, resume_value, *, state=None, expected_version=None):
        if self._resume_error is not None:
            raise self._resume_error
        raise AssertionError("FakeGraph.resume 不应成功返回")

    def resume_stream(self, thread_id, resume_value, *, state=None, expected_version=None):
        if self._resume_error is not None:
            raise self._resume_error
        raise AssertionError("FakeGraph.resume_stream 不应成功返回")


class FakeBusyLease:
    """``acquire`` 直接抛 CONVERSATION_BUSY，模拟同会话并发被拒。"""

    def acquire(self, thread_id, *, run_id=None, ttl_seconds=None):
        raise AgentSessionError(
            AgentSessionErrorCode.CONVERSATION_BUSY,
            f"conversation {thread_id} is busy",
        )

    def renew(self, thread_id, run_id, fence, *, ttl_seconds=None):
        raise StaleAgentRunError(thread_id)

    def release(self, thread_id, run_id) -> None:
        return None


# ───────────────────────── 进程内计数器 ─────────────────────────


def test_in_memory_counter_increments_and_reads():
    c = InMemoryCounter("x_total")
    assert c.value() == 0
    c.inc()
    c.inc(2)
    assert c.value() == 3


def test_root_span_records_only_safe_metadata():
    """root span 只带元数据，绝不记录 PII（prompt / 住户聊天 / 地址 / tool 参数）。"""
    obs = AgentObservability.in_memory()
    with obs.observe_turn(
        conversation_id="conv-9",
        run_id=uuid4(),
        fence=7,
        expected_version=3,
        confirmed=True,
    ) as span:
        assert isinstance(span, NullSpan)
        span.set_attribute("agent.intent", "REPAIR")
        span.set_attribute("agent.degraded", True)

    attrs = span.attributes
    assert attrs["agent.conversation.id"] == "conv-9"
    assert attrs["agent.lease.fence"] == 7
    assert attrs["agent.checkpoint.expected_version"] == 3
    assert attrs["agent.confirmed"] is True
    assert attrs["agent.intent"] == "REPAIR"
    assert "agent.runtime.version" in attrs
    # 明确不应出现 PII 类键
    for forbidden in ("prompt", "message", "address", "phone", "argument", "result"):
        assert not any(forbidden in key for key in attrs), f"span 泄漏敏感键: {attrs}"


def test_build_without_otel_falls_back_to_in_memory():
    """未安装 opentelemetry 时 build 降级为进程内计数器，仍可断言递增。"""
    obs = AgentObservability.build(settings)
    assert isinstance(obs.metrics.conversation_busy, InMemoryCounter)
    obs.metrics.conversation_busy.inc()
    assert obs.metrics.conversation_busy.value() == 1


# ───────────────────────── runner 指标集成 ─────────────────────────


def test_conversation_busy_metric_on_lease_collision(session_factory, ctx):
    runner, *_ = boot(session_factory)
    runner._run_lease = FakeBusyLease()  # 强制 acquire 抛 CONVERSATION_BUSY
    with pytest.raises(AgentSessionError) as exc:
        runner.start(
            conversation_id="busy-1",
            context=ctx,
            user_text="我要报修",
            house_id=next(iter(ctx.house_ids)),
            slots=dict(REPAIR_SLOTS),
        )
    assert exc.value.code == AgentSessionErrorCode.CONVERSATION_BUSY
    assert runner._observability.metrics.conversation_busy.value() == 1
    # 其它指标不受影响
    assert runner._observability.metrics.checkpoint_conflict.value() == 0
    assert runner._observability.metrics.stale_fence_rejected.value() == 0


def test_checkpoint_conflict_metric_on_stale_cas(session_factory, ctx):
    runner, *_ = boot(session_factory)
    runner._enforce = False  # 不走 lease DB 路径，直接触发 graph 错误
    runner._graph = FakeGraph(invoke_error=CheckpointVersionConflict("cp-1", 1))
    with pytest.raises(CheckpointVersionConflict):
        runner.start(
            conversation_id="cp-1",
            context=ctx,
            user_text="你好",
            house_id=next(iter(ctx.house_ids)),
        )
    assert runner._observability.metrics.checkpoint_conflict.value() == 1


def test_stale_fence_metric_on_stale_run(session_factory, ctx):
    runner, *_ = boot(session_factory)
    runner._enforce = False
    runner._graph = FakeGraph(invoke_error=StaleAgentRunError("sf-1"))
    with pytest.raises(StaleAgentRunError):
        runner.start(
            conversation_id="sf-1",
            context=ctx,
            user_text="你好",
            house_id=next(iter(ctx.house_ids)),
        )
    assert runner._observability.metrics.stale_fence_rejected.value() == 1


class _FakeRecovery:
    """返回一棵可恢复状态，避开 DB checkpoint 读取。"""

    def restore(self, conversation_id, ctx, expected_action_hash=None):
        return SimpleNamespace(state=_minimal_state())


def test_approval_rollback_metric_on_confirmed_failure(session_factory, ctx):
    runner, *_ = boot(session_factory)
    runner._enforce = False
    runner._recovery = _FakeRecovery()
    # 换上会在 resume 失败的图，并给一个 token provider（模拟审批已签发/消费）
    runner._graph = FakeGraph(resume_error=RuntimeError("business failed"))
    runner._confirmation_token_provider = lambda state: "tok-x"
    with pytest.raises(RuntimeError):
        runner.resume(
            conversation_id="apr-1",
            context=ctx,
            confirmed=True,
            confirmation_token="tok-x",
        )
    assert runner._observability.metrics.approval_rollback.value() == 1


def test_approval_rollback_not_counted_on_unconfirmed_failure(session_factory, ctx):
    """未确认（取消）路径失败不应计入 approval_rollback。"""
    runner, *_ = boot(session_factory)
    runner._enforce = False
    runner._recovery = _FakeRecovery()
    runner._graph = FakeGraph(resume_error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        runner.resume(
            conversation_id="apr-2",
            context=ctx,
            confirmed=False,
        )
    assert runner._observability.metrics.approval_rollback.value() == 0


def test_compiled_graph_invoke_stream_yields_lifecycle_events():
    """图内核流式化：invoke_stream 逐节点 yield 生命周期事件并最终给出结果。"""
    graph = CompiledGraph(StateGraph(), None)  # 空图也能跑完
    state = _minimal_state()
    events = list(graph.invoke_stream(state, thread_id="t1"))
    assert events[-1][0] == "__final__"
    assert events[-1][1]["done"] is True
    assert events[-1][1]["state"] is state


def _minimal_state():
    from uuid import UUID

    from property_agent.agent.state import GraphState

    return GraphState(
        conversation_id="t1",
        actor_id=UUID("00000000-0000-0000-0000-000000000001"),
        community_id=UUID("00000000-0000-0000-0000-000000000002"),
        current_house_id=UUID("00000000-0000-0000-0000-000000000003"),
        intent="REPAIR",
        slots={},
    )
