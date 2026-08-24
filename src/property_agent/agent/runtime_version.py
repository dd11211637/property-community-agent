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

from enum import StrEnum


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
    """服务端注入的 runtime 选择策略（公网 0%）。

    只负责「新会话选谁」。已钉住的会话由持久化列决定，本策略不参与。
    ``enabled`` 必须由测试 / 内部 pilot 显式注入；默认值构成公网硬 0%。
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = bool(enabled)

    @property
    def v2_enabled(self) -> bool:
        return self._enabled

    def select_new(self) -> AgentRuntimeVersion:
        """为新会话选择 runtime；默认 v1（公网硬 0% v2）。"""
        return AgentRuntimeVersion.V2 if self._enabled else AgentRuntimeVersion.V1

    def select_for(self, persisted_version: str | None) -> AgentRuntimeVersion:
        """恢复 / 查询时一律服从持久化版本，永不切换。"""
        return AgentRuntimeVersion.from_str(persisted_version)
