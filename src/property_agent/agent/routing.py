"""路由装配 — PRD §6.5.3。

把"意图 → 子图入口"的映射与"工具注册表"的合并集中在一处，方便主图与
API 层复用；同时保证一个意图只会进入自己的子图，不会串模块。
"""

from collections.abc import Mapping
from typing import Any

from property_agent.agent.policies import Intent

# 意图 -> 子图命名空间
INTENT_SUBGRAPH: dict[str, str] = {
    Intent.REPAIR.value: "repair",
    Intent.ANNOUNCEMENT.value: "announcement",
    Intent.BILLING.value: "billing",
    Intent.INSPECTION.value: "inspection",
}


def subgraph_entry(intent: str | None) -> str | None:
    """返回意图对应的子图入口节点名；无对应子图返回 None。"""
    name = INTENT_SUBGRAPH.get(intent or "")
    return f"{name}.prepare" if name == "inspection" else (f"{name}.select_tool" if name else None)


def merge_registries(*registries: Mapping[str, Any]) -> dict[str, Any]:
    """合并各模块工具注册表；重复工具名视为装配错误。"""
    merged: dict[str, Any] = {}
    for registry in registries:
        for tool_name, tool in registry.items():
            if tool_name in merged:
                raise ValueError(f"duplicated tool name: {tool_name}")
            merged[tool_name] = tool
    return merged
