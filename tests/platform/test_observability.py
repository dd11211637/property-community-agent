from __future__ import annotations

import json
import logging

from property_agent.platform.observability import JsonFormatter


def test_json_formatter_emits_trace_fields_without_message_mutation():
    record = logging.LogRecord(
        name="property_agent.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="http_request",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-test"
    record.route = "/api/bills/{bill_id}"
    record.status_code = 200
    record.duration_ms = 12.5

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "http_request"
    assert payload["request_id"] == "req-test"
    assert payload["route"] == "/api/bills/{bill_id}"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 12.5
