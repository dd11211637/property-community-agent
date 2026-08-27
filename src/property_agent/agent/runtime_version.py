"""Runtime 版本钉死与选择策略 — PR4 §8 / §9。

``runtime_version`` 在会话创建时由服务端 ``RuntimeSelectionPolicy`` 钉死，
之后整个生命周期（ACTIVE / WAITING_CONFIRM / HANDOVER / CLOSED）**永不切换**：

* 新会话：lease 获取后由 ``RuntimeSelectionPolicy.select_new()`` 决定 v1 / v2，
  在 ``ConversationService.start`` 的 INSERT 中持久化；
* 已存在会话：始终使用持久化的 ``runtime_version``；
* 客户端 / 请求体 / 模型输出 / AgentState / checkpoint / slots 都不能指定 runtime。

生产装配始终使用默认策略（公网硬 0%）。只有测试 / 内部 pilot 显式注入
``enabled=True`` 时才可能新建 v2 会话；已钉 v2 的会话继续由 v2 执行。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from enum import StrEnum
from threading import RLock
from uuid import UUID

from property_agent.agent.runtime_rollout import (
    RolloutConfig,
    RolloutControl,
    RuntimeAssignment,
    RuntimeEligibility,
    decide_assignment,
)

AssignmentObserver = Callable[[RuntimeAssignment], None]
CommunityPolicy = Callable[[UUID], bool]


class AgentRuntimeVersion(StrEnum):
    """规范 runtime 版本。v1 = legacy 自定义运行时；v2 = 官方 LangGraph 运行时。"""

    V1 = "v1"
    V2 = "v2"

    @classmethod
    def from_str(cls, value: str | None) -> AgentRuntimeVersion:
        if value is None:
            return cls.V1
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"unsupported persisted agent runtime version: {value!r}")


class RuntimeSelectionPolicy:
    """Selects only not-yet-persisted conversations from trusted structural facts."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        control: RolloutControl | None = None,
        eligibility: RuntimeEligibility | None = None,
        community_policy: CommunityPolicy | None = None,
        assignment_observer: AssignmentObserver | None = None,
    ) -> None:
        self._legacy_enabled = bool(enabled)
        self._control = control or RolloutControl(RolloutConfig())
        self._eligibility = eligibility or RuntimeEligibility()
        self._community_policy = community_policy or (lambda _community_id: True)
        self._assignment_observer = assignment_observer
        self._eligibility_lock = RLock()

    @property
    def v2_enabled(self) -> bool:
        return self._legacy_enabled or self._control.config.basis_points > 0

    @property
    def rollout_control(self) -> RolloutControl:
        return self._control

    def select_new(
        self,
        *,
        community_id: UUID | None = None,
        actor_id: UUID | None = None,
        conversation_id: str | None = None,
    ) -> AgentRuntimeVersion:
        """Compatibility facade plus trusted-input PR7-C assignment entrypoint."""
        if self._legacy_enabled:
            return AgentRuntimeVersion.V2
        if community_id is None or actor_id is None or conversation_id is None:
            return AgentRuntimeVersion.V1
        return AgentRuntimeVersion(
            self.decide_new(
                community_id=community_id,
                actor_id=actor_id,
                conversation_id=conversation_id,
            ).runtime_version
        )

    def decide_new(
        self,
        *,
        community_id: UUID,
        actor_id: UUID,
        conversation_id: str,
    ) -> RuntimeAssignment:
        with self._eligibility_lock:
            eligibility = replace(
                self._eligibility,
                community_policy_included=self._community_policy(community_id),
            )
        decision = decide_assignment(
            self._control.config,
            eligibility,
            community_id=community_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
        )
        if self._assignment_observer is not None:
            self._assignment_observer(decision)
        return decision

    def update_authoritative_readiness(
        self,
        *,
        accepted_head_available: bool,
    ) -> None:
        """Refresh the live accepted-head fact from the server readiness probe."""
        with self._eligibility_lock:
            self._eligibility = replace(
                self._eligibility,
                accepted_head_available=accepted_head_available,
            )

    def readiness(self) -> dict[str, str | int | bool]:
        config = self._control.config
        with self._eligibility_lock:
            reason = self._eligibility.reason()
        ready = config.basis_points == 0 or reason.value == "eligible"
        state = "OPTIONAL_ZERO" if config.basis_points == 0 else "READY"
        if not ready:
            state = "NOT_READY"
        return {
            "state": state,
            "ready": ready,
            "rollout_basis_points": config.basis_points,
            "config_version": config.config_version,
            "salt_version": config.salt_version,
            "eligibility_policy_version": config.eligibility_policy_version,
            "fallback_runtime": config.fallback_runtime,
            "reason": "rollout_zero" if config.basis_points == 0 else reason.value,
        }

    def select_for(self, persisted_version: str | None) -> AgentRuntimeVersion:
        """恢复 / 查询时一律服从持久化版本，永不切换。"""
        return AgentRuntimeVersion.from_str(persisted_version)
