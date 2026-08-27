"""Attach a safe PR7-A span receipt to one exact chaos pytest execution."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from property_agent.agent.observability import AgentObservability


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    campaign_id = os.getenv("PR7B_CHAOS_CAMPAIGN_ID", "")
    case_id = os.getenv("PR7B_CHAOS_CASE_ID", "")
    receipt_path = os.getenv("PR7B_CHAOS_RECEIPT_PATH", "")
    if not campaign_id or not case_id or not receipt_path:
        yield
        return
    observation = AgentObservability.in_memory()
    with observation.span("chaos.drill", attributes={"certification.chaos.case": case_id}):
        outcome = yield
    if outcome.excinfo is None:
        span = observation.spans[-1]
        payload = {
            "campaign_id": span.attributes.get("certification.campaign.id", ""),
            "case_id": span.attributes.get("certification.chaos.case", ""),
            "pytest_node_id": item.nodeid,
            "span_name": span.name,
        }
        Path(receipt_path).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
