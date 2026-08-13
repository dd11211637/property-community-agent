"""Two-phase real-API check for durable Agent pending confirmations.

This is testing/support code and is never imported by the production package.  Run
``prepare`` before restarting the backend container and ``verify`` after it is healthy.
The state file contains only demo identifiers and must be placed in a temporary path.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


def _login(client: httpx.Client) -> dict[str, Any]:
    response = client.post("/api/auth/login", json={"username": "zhangsan", "password": "123456"})
    response.raise_for_status()
    return response.json()


def _headers(session: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {session['access_token']}",
        "X-Current-House-Id": str(session["current_house_id"]),
    }


def _data(response: httpx.Response) -> Any:
    response.raise_for_status()
    payload = response.json()
    assert payload["success"] is True, payload
    return payload["data"]


def prepare(base_url: str, state_path: Path) -> None:
    conversation_id = f"restart-{uuid4().hex}"
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=20) as client:
        session = _login(client)
        headers = _headers(session)
        before = _data(client.get("/api/work-orders", headers=headers))["items"]
        turn = _data(
            client.post(
                f"/api/agent/conversations/{conversation_id}/messages",
                headers=headers,
                json={"text": "客厅电灯坏了，需要报修"},
            )
        )
        pending = turn["pending_confirmation"]
        assert pending is not None, turn
        state_path.write_text(
            json.dumps(
                {
                    "conversation_id": conversation_id,
                    "action_hash": pending["action_hash"],
                    "work_order_count": len(before),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    print(json.dumps({"phase": "prepared", "conversation_id": conversation_id}))


def verify(base_url: str, state_path: Path) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    conversation_id = state["conversation_id"]
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=20) as client:
        session = _login(client)
        headers = _headers(session)
        status = _data(client.get(f"/api/agent/conversations/{conversation_id}", headers=headers))
        pending = status["pending_confirmation"]
        assert pending is not None, status
        assert pending["action_hash"] == state["action_hash"]
        turn = _data(
            client.post(
                f"/api/agent/conversations/{conversation_id}/confirmations",
                headers=headers,
                json={
                    "confirmed": False,
                    "action_hash": state["action_hash"],
                },
            )
        )
        assert turn["pending_confirmation"] is None, turn
        after = _data(client.get("/api/work-orders", headers=headers))["items"]
        assert len(after) == state["work_order_count"]
    state_path.unlink(missing_ok=True)
    print(json.dumps({"phase": "verified", "conversation_id": conversation_id}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "verify"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "prepare":
        prepare(args.base_url, args.state)
    else:
        verify(args.base_url, args.state)


if __name__ == "__main__":
    main()
