"""四个业务子图 — PRD §6.5.3。"""

from property_agent.agent.subgraphs.announcement import (
    attach_announcement_subgraph,
    select_announcement_tool,
)
from property_agent.agent.subgraphs.base import attach_subgraph
from property_agent.agent.subgraphs.billing import (
    attach_billing_subgraph,
    select_billing_tool,
)
from property_agent.agent.subgraphs.inspection import (
    attach_inspection_subgraph,
    select_inspection_tool,
)
from property_agent.agent.subgraphs.repair import (
    attach_repair_subgraph,
    select_repair_tool,
)

__all__ = [
    "attach_announcement_subgraph",
    "attach_billing_subgraph",
    "attach_inspection_subgraph",
    "attach_repair_subgraph",
    "attach_subgraph",
    "select_announcement_tool",
    "select_billing_tool",
    "select_inspection_tool",
    "select_repair_tool",
]
