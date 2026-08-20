"""Agent 运行时可观测性 — P1 观测与流式（审查报告 P1 §OTel tracing）。

提供两类运行时遥测，直接呼应 P0 正确性底座是否真的生效：

* **4 个关键计数器**（审查报告最强调的产出）：
  ``agent_conversation_busy_total``      —— 同会话并发被 409 CONVERSATION_BUSY 拒绝；
  ``agent_checkpoint_conflict_total``    —— checkpoint CAS 命中 stale 版本被拒；
  ``agent_stale_fence_rejected_total``   —— 旧 worker 凭旧 fence 的业务写被 fencing 拒绝；
  ``agent_approval_rollback_total``      —— 已消费的审批因后续业务失败整体回滚。

* **``agent.turn`` root span**：把一轮对话（conversation acquire lease → graph →
  checkpoint）串成一条 trace，便于关联 model / tool / approval / checkpoint。

设计约束（与审查报告一致）：

* **不记录 PII**：span 只带 conversation_id / run_id / fence / expected_version /
  result_version / confirmed / degraded 等元数据，绝不记录完整 prompt、住户聊天、
  手机号、房屋地址、tool 原始参数或结果。
* **OTel 可选**：生产装了 ``opentelemetry-api``/``opentelemetry-sdk`` 且 ``otel_enabled``
  时，计数器额外推送到 OTel meter、span 接入全局 TracerProvider；未安装则降级为
  **进程内计数器 + NullTracer**，保证本地 / SQLite 测试仍可断言指标递增、span 属性可读。
  降级路径不依赖网络，也不影响正确性底座。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from property_agent.config import Settings

# 静态运行时版本，供 span 标注（便于跨版本关联 trace）。
AGENT_RUNTIME_VERSION = "p0-p1-observability"


class Counter:
    """计数器接口：进程内读数用于测试，OTel 路径用于生产导出。"""

    def inc(self, amount: int = 1) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def value(self) -> int:  # pragma: no cover - interface
        raise NotImplementedError


class InMemoryCounter(Counter):
    """进程内单调计数器；本地/测试中可读，OTel 缺失时作为唯一实现。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self._value = 0

    def inc(self, amount: int = 1) -> None:
        self._value += amount

    def value(self) -> int:
        return self._value


class DualCounter(InMemoryCounter):
    """OTel meter 可用时，递增同时推送到 OTel counter（export 给采集端）。

    进程内读数仍来自父类，因此测试可读；OTel 推送失败静默忽略，不影响业务。
    """

    def __init__(self, name: str, otel_counter: Any) -> None:
        super().__init__(name)
        self._otel = otel_counter

    def inc(self, amount: int = 1) -> None:
        super().inc(amount)
        try:
            self._otel.add(amount)
        except Exception:  # pragma: no cover - 采集端异常不应影响业务
            pass


@dataclass
class Metrics:
    """P0 正确性底座的四个验证指标。"""

    conversation_busy: Counter
    checkpoint_conflict: Counter
    stale_fence_rejected: Counter
    approval_rollback: Counter


class NullSpan:
    """无导出时的 span 占位：记录 set_attribute 以便测试断言。"""

    def __init__(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.name = name
        self.attributes: dict[str, Any] = dict(attributes or {})

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class NullTracer:
    """无 OTel 时的 tracer：返回 NullSpan 的上下文管理器。"""

    @contextmanager
    def start_as_current_span(
        self, name: str, attributes: dict[str, Any] | None = None
    ) -> Iterator[NullSpan]:
        yield NullSpan(name, attributes)


def _try_otel() -> tuple[Any, Any]:
    """延迟探测 OTel SDK；缺失或导入失败一律返回 (None, None)。"""
    try:
        from opentelemetry import metrics as otel_metrics
        from opentelemetry import trace as otel_trace

        return otel_metrics, otel_trace
    except Exception:  # pragma: no cover - 依赖可选
        return None, None


def _build_metrics() -> Metrics:
    otel_metrics, _ = _try_otel()
    if otel_metrics is None:
        return _in_memory_metrics()
    try:  # pragma: no cover - 依赖可选
        meter = otel_metrics.get_meter("property-agent")
        return Metrics(
            conversation_busy=DualCounter(
                "agent_conversation_busy_total",
                meter.create_counter("agent_conversation_busy_total"),
            ),
            checkpoint_conflict=DualCounter(
                "agent_checkpoint_conflict_total",
                meter.create_counter("agent_checkpoint_conflict_total"),
            ),
            stale_fence_rejected=DualCounter(
                "agent_stale_fence_rejected_total",
                meter.create_counter("agent_stale_fence_rejected_total"),
            ),
            approval_rollback=DualCounter(
                "agent_approval_rollback_total",
                meter.create_counter("agent_approval_rollback_total"),
            ),
        )
    except Exception:  # pragma: no cover - 依赖可选
        return _in_memory_metrics()


def _build_tracer(settings: Settings) -> Any:
    _, otel_trace = _try_otel()
    if otel_trace is None:
        return NullTracer()
    try:  # pragma: no cover - 依赖可选
        provider = otel_trace.get_tracer_provider()
        return provider.get_tracer(settings.otel_service_name or "property-agent")
    except Exception:  # pragma: no cover - 依赖可选
        return NullTracer()


def _in_memory_metrics() -> Metrics:
    return Metrics(
        conversation_busy=InMemoryCounter("agent_conversation_busy_total"),
        checkpoint_conflict=InMemoryCounter("agent_checkpoint_conflict_total"),
        stale_fence_rejected=InMemoryCounter("agent_stale_fence_rejected_total"),
        approval_rollback=InMemoryCounter("agent_approval_rollback_total"),
    )


@dataclass
class AgentObservability:
    """运行时遥测聚合：4 指标 + root span tracer。"""

    metrics: Metrics
    tracer: Any
    enabled: bool
    degraded: bool = False

    @classmethod
    def in_memory(cls) -> AgentObservability:
        """进程内实现（默认 / 测试 / 无 OTel 环境），标记 degraded。"""
        return cls(
            metrics=_in_memory_metrics(),
            tracer=NullTracer(),
            enabled=True,
            degraded=True,
        )

    @classmethod
    def build(cls, settings: Settings) -> AgentObservability:
        """按配置构建：关闭或缺失 OTel 时降级为进程内实现（degraded=True）。"""
        if not getattr(settings, "otel_enabled", True):
            return cls.in_memory()
        otel_metrics, otel_trace = _try_otel()
        if otel_metrics is None or otel_trace is None:
            return cls.in_memory()
        return cls(
            metrics=_build_metrics(),
            tracer=_build_tracer(settings),
            enabled=True,
            degraded=False,
        )

    @contextmanager
    def observe_turn(
        self,
        *,
        conversation_id: str,
        run_id: Any | None = None,
        fence: int | None = None,
        expected_version: int | None = None,
        confirmed: bool = False,
    ) -> Iterator[Any]:
        """开启一轮 ``agent.turn`` root span，返回可 set_attribute 的 span。"""
        attributes: dict[str, Any] = {
            "agent.conversation.id": str(conversation_id),
            "agent.runtime.version": AGENT_RUNTIME_VERSION,
            "agent.confirmed": confirmed,
        }
        if run_id is not None:
            attributes["agent.run.id"] = str(run_id)
        if fence is not None:
            attributes["agent.lease.fence"] = fence
        if expected_version is not None:
            attributes["agent.checkpoint.expected_version"] = expected_version
        with self.tracer.start_as_current_span("agent.turn", attributes=attributes) as span:
            yield span


__all__ = [
    "AGENT_RUNTIME_VERSION",
    "AgentObservability",
    "Counter",
    "InMemoryCounter",
    "Metrics",
    "NullSpan",
    "NullTracer",
]
