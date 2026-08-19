"""轻量图内核 — LangGraph 形态的 StateGraph / 中断 / 检查点。

PRD §6.5 指定使用 LangGraph 做编排。本内核实现 LangGraph 在 MVP 中真正需要的
语义子集（节点、边、条件边、interrupt 暂停/恢复、Checkpointer 持久化），
不引入 langgraph 重依赖，便于在无 LLM Key 的环境跑通与测试；``ModelGateway``
是接入真实模型的接缝。若后续引入 langgraph，只需把 ``StateGraph``/``CompiledGraph``
替换为官方实现，节点与状态契约保持不变。

设计要点：
* 节点返回更新后的 ``GraphState``（原地修改并返回自身）。
* 条件边由 router(state) -> 下一节点名 决定分支。
* ``interrupt(payload)`` 暂停流程并把状态写入 Checkpointer；``resume`` 从
  中断节点恢复，并把用户决策放入 ``state._resume``。写操作确认卡在 interrupt
  之前构造，因此 interrupt 之前**不会**调用任何业务写 Service（PRD A-03）。
"""

from collections.abc import Callable
from typing import Any, Protocol
from uuid import uuid4

from property_agent.agent.state import GraphState


class Interrupt(Exception):
    """由节点抛出以暂停图执行并等待外部（用户/人工）恢复。"""

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        super().__init__("graph interrupt")


def interrupt(payload: Any) -> None:
    """在节点中调用以暂停并等待恢复（PRD §6.5.7 确认中断）。"""
    raise Interrupt(payload)


class Checkpointer(Protocol):
    def save(
        self, thread_id: str, state: GraphState, *, expected_version: int | None = None
    ) -> None: ...

    def load(self, thread_id: str) -> GraphState | None: ...

    def list_threads(self) -> list[str]: ...


class MemoryCheckpointer:
    """内存 Checkpointer（单元测试与演示用，PRD §6.5.8）。"""

    def __init__(self) -> None:
        self._store: dict[str, GraphState] = {}

    def save(
        self, thread_id: str, state: GraphState, *, expected_version: int | None = None
    ) -> None:
        # 内存实现不做 CAS：单写者由调用方（run lease）保证。
        self._store[thread_id] = state

    def load(self, thread_id: str) -> GraphState | None:
        return self._store.get(thread_id)

    def list_threads(self) -> list[str]:
        return list(self._store.keys())


Node = Callable[[GraphState], GraphState]
Router = Callable[[GraphState], str | None]


class StateGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, str] = {}
        self._conditional: dict[str, Router] = {}
        self._entry: str | None = None
        self._finish: str | None = None

    def add_node(self, name: str, func: Node) -> "StateGraph":
        self._nodes[name] = func
        return self

    def add_edge(self, source: str, target: str) -> "StateGraph":
        self._edges[source] = target
        return self

    def add_conditional_edges(self, source: str, router: Router) -> "StateGraph":
        self._conditional[source] = router
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        self._entry = name
        return self

    def set_finish_point(self, name: str) -> "StateGraph":
        self._finish = name
        return self

    def compile(self, checkpointer: Checkpointer | None = None) -> "CompiledGraph":
        if self._entry is None:
            raise ValueError("StateGraph has no entry point.")
        return CompiledGraph(self, checkpointer)


class CompiledGraph:
    def __init__(self, graph: StateGraph, checkpointer: Checkpointer | None) -> None:
        self._g = graph
        self._cp = checkpointer

    def invoke(
        self,
        state: GraphState,
        *,
        thread_id: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        thread_id = thread_id or state.conversation_id or str(uuid4())
        return self._run(self._g._entry, state, thread_id, expected_version)

    def resume(
        self,
        thread_id: str,
        resume_value: Any,
        *,
        state: GraphState | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """从中断点恢复。

        ``state`` 显式传入时以它为准——恢复守卫（PRD §6.5.8 的会话/房屋/有效期
        三项校验）会先加载并校正快照身份，直接复用其结果可避免二次读取
        导致校验后的状态被旧快照覆盖。

        ``expected_version`` 为调用方在 **turn 开始** 时读取的检查点版本，用于
        checkpoint CAS：若本 run 拿到的快照已 stale（例如 lease 过期后被新 run
        覆盖），CAS 失败抛 ``CheckpointVersionConflict``，由 runner 终止本 run。
        """
        if state is None:
            if self._cp is None:
                raise RuntimeError("No checkpointer configured; cannot resume.")
            state = self._cp.load(thread_id)
        if state is None:
            raise RuntimeError(f"No checkpoint found for thread {thread_id}.")
        state._resume = resume_value
        return self._run(
            state._interrupt_node or self._g._entry, state, thread_id, expected_version
        )

    # ---- 内部执行 ----
    def _run(
        self,
        start: str | None,
        state: GraphState,
        thread_id: str,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        current = start
        while current is not None:
            node = self._g._nodes.get(current)
            if node is None:
                break
            try:
                state = node(state)
            except Interrupt as intr:
                state._interrupt_node = current
                if self._cp is not None:
                    self._cp.save(thread_id, state, expected_version=expected_version)
                return {
                    "state": state,
                    "interrupt": intr.payload,
                    "thread_id": thread_id,
                    "done": False,
                }

            if current in self._g._conditional:
                nxt = self._g._conditional[current](state)
                if nxt is None or nxt == self._g._finish:
                    return self._finish(state, thread_id, expected_version)
                current = nxt
            elif current in self._g._edges:
                current = self._g._edges[current]
                if current == self._g._finish:
                    return self._finish(state, thread_id, expected_version)
            else:
                if current == self._g._finish:
                    return self._finish(state, thread_id, expected_version)
                break
        return self._finish(state, thread_id, expected_version)

    def _finish(
        self, state: GraphState, thread_id: str, expected_version: int | None = None
    ) -> dict[str, Any]:
        # 本轮结束即清理恢复态，避免下一轮从检查点恢复时被误判为"已确认"
        state._resume = None
        state._interrupt_node = None
        if self._cp is not None:
            self._cp.save(thread_id, state, expected_version=expected_version)
        return {"state": state, "interrupt": None, "thread_id": thread_id, "done": True}
