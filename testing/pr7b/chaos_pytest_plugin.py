"""Capture telemetry emitted by the faulted production component itself."""

from __future__ import annotations

import json
import os
from inspect import currentframe
from pathlib import Path

import pytest

from property_agent.agent.observability import AgentObservability, InMemoryCounter
from testing.pr7b.chaos_signals import matching_signals


def _production_caller_module() -> str:
    frame = currentframe()
    caller = frame.f_back.f_back if frame is not None and frame.f_back is not None else None
    module = str(caller.f_globals.get("__name__", "")) if caller is not None else ""
    return module if module.startswith("property_agent.") else ""


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    campaign_id = os.getenv("PR7B_CHAOS_CAMPAIGN_ID", "")
    case_id = os.getenv("PR7B_CHAOS_CASE_ID", "")
    receipt_path = os.getenv("PR7B_CHAOS_RECEIPT_PATH", "")
    target_node_id = os.getenv("PR7B_CHAOS_TARGET_NODE_ID", "")
    if not campaign_id or not case_id or not receipt_path:
        yield
        return
    signals = []
    original_count = AgentObservability.count
    original_inc = InMemoryCounter.inc

    def captured_count(self, name, *, amount=1, attributes=None):
        source_module = _production_caller_module()
        signals.append(
            {
                "name": name,
                "attributes": self.metric_attributes(attributes),
                "production_origin": bool(source_module),
                "source_module": source_module,
            }
        )
        return original_count(self, name, amount=amount, attributes=attributes)

    def captured_inc(self, amount=1, attributes=None):
        source_module = _production_caller_module()
        signals.append(
            {
                "name": self.name,
                "attributes": dict(attributes or {}),
                "production_origin": bool(source_module),
                "source_module": source_module,
            }
        )
        return original_inc(self, amount, attributes)

    AgentObservability.count = captured_count
    InMemoryCounter.inc = captured_inc
    try:
        outcome = yield
    finally:
        AgentObservability.count = original_count
        InMemoryCounter.inc = original_inc
    matches = matching_signals(case_id, signals)
    if outcome.excinfo is None and matches:
        payload = {
            "campaign_id": campaign_id,
            "case_id": case_id,
            "pytest_node_id": target_node_id or item.nodeid,
            "actual_component_signals": [
                {
                    "name": match["name"],
                    "attributes": match["attributes"],
                    "source_module": match["source_module"],
                }
                for match in matches
            ],
        }
        Path(receipt_path).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
