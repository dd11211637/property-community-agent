"""Repeatable real-API smoke flow for the Docker demo environment.

This script is demo/support code. It talks only to the public HTTP API and intentionally
creates business records in ``property_agent_demo``. Run the guarded reset first when a
clean, deterministic demonstration is required.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


def _idem(prefix: str) -> str:
    return f"e2e-{prefix}-{uuid4().hex}"


@dataclass
class Actor:
    username: str
    token: str
    actor_id: str
    community_id: str
    house_ids: list[str]
    current_house_id: str | None

    def headers(self, *, idem: str | None = None, house: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if idem:
            headers["Idempotency-Key"] = idem
        if house and self.current_house_id:
            headers["X-Current-House-Id"] = self.current_house_id
        return headers


class DemoApi:
    def __init__(self, base_url: str, *, client: Any | None = None) -> None:
        self.client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=20)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def login(self, username: str) -> Actor:
        response = self.client.post(
            "/api/auth/login", json={"username": username, "password": "123456"}
        )
        response.raise_for_status()
        data = response.json()
        return Actor(
            username=username,
            token=data["access_token"],
            actor_id=str(data["actor_id"]),
            community_id=str(data["community_id"]),
            house_ids=[str(value) for value in data["house_ids"]],
            current_house_id=(str(data["current_house_id"]) if data["current_house_id"] else None),
        )

    def request(
        self,
        method: str,
        path: str,
        actor: Actor,
        *,
        body: dict[str, Any] | None = None,
        idem: str | None = None,
        house: bool = False,
        expected_status: int = 200,
    ) -> Any:
        response = self.client.request(
            method,
            path,
            headers=actor.headers(idem=idem, house=house),
            json=body,
        )
        if response.status_code != expected_status:
            raise AssertionError(
                f"{method} {path}: expected {expected_status}, got {response.status_code}: "
                f"{response.text}"
            )
        payload = response.json()
        if isinstance(payload, dict) and "success" in payload:
            if not payload["success"]:
                raise AssertionError(f"{method} {path}: {payload}")
            return payload["data"]
        return payload

    def confirmation(self, actor: Actor, action: str, parameters: dict[str, Any]) -> str:
        data = self.request(
            "POST",
            "/api/confirmations",
            actor,
            body={"action": action, "parameters": parameters},
        )
        return str(data["token"] if isinstance(data, dict) else data)


def run(base_url: str, *, client: Any | None = None) -> dict[str, Any]:
    api = DemoApi(base_url, client=client)
    try:
        resident = api.login("zhangsan")
        multi = api.login("lisi")
        customer_service = api.login("customer_service")
        worker = api.login("repair_worker")
        finance = api.login("finance")
        guard = api.login("security_guard")
        manager = api.login("manager")

        assert resident.current_house_id and len(resident.house_ids) == 1
        assert multi.current_house_id is None and len(multi.house_ids) == 2
        selected = api.request(
            "POST", "/api/auth/house", multi, body={"house_id": multi.house_ids[1]}
        )
        assert str(selected["house_id"]) == multi.house_ids[1]

        # Repair: create -> assign -> accept -> progress -> completion -> rework -> completion
        # -> acceptance -> review.
        repair_parameters = {
            "house_id": resident.current_house_id,
            "category": "WATER_PLUMBING",
            "location": "1栋1单元101厨房",
            "description": "E2E：厨房水管漏水",
            "urgency": "NORMAL",
            "appointment_at": "2026-09-01T15:00:00+08:00",
            "attachment_ids": [],
        }
        repair_token = api.confirmation(resident, "CREATE_WORK_ORDER", repair_parameters)
        repair = api.request(
            "POST",
            "/api/work-orders",
            resident,
            body={**repair_parameters, "confirmation_token": repair_token},
            idem=_idem("repair-create"),
            expected_status=201,
        )
        repair_id = str(repair["id"])
        repair = api.request(
            "POST",
            f"/api/work-orders/{repair_id}/actions/assign",
            manager,
            body={"expected_version": repair["version"], "assignee_id": worker.actor_id},
            idem=_idem("repair-assign"),
        )
        repair = api.request(
            "POST",
            f"/api/work-orders/{repair_id}/actions/accept",
            worker,
            body={"expected_version": repair["version"]},
            idem=_idem("repair-accept"),
        )
        repair = api.request(
            "POST",
            f"/api/work-orders/{repair_id}/actions/record-progress",
            worker,
            body={
                "expected_version": repair["version"],
                "record_type": "PROGRESS",
                "note": "已更换水管接头",
            },
            idem=_idem("repair-progress"),
        )
        repair = api.request(
            "POST",
            f"/api/work-orders/{repair_id}/actions/submit-completion",
            worker,
            body={"expected_version": repair["version"], "note": "首次维修完成"},
            idem=_idem("repair-complete-1"),
        )
        repair = api.request(
            "POST",
            f"/api/work-orders/{repair_id}/actions/request-rework",
            resident,
            body={"expected_version": repair["version"], "reason": "仍有轻微渗水"},
            idem=_idem("repair-rework"),
        )
        repair = api.request(
            "POST",
            f"/api/work-orders/{repair_id}/actions/submit-completion",
            worker,
            body={"expected_version": repair["version"], "note": "返工后复检无渗漏"},
            idem=_idem("repair-complete-2"),
        )
        repair = api.request(
            "POST",
            f"/api/work-orders/{repair_id}/actions/verify-pass",
            resident,
            body={"expected_version": repair["version"]},
            idem=_idem("repair-verify"),
        )
        repair = api.request(
            "POST",
            f"/api/work-orders/{repair_id}/reviews",
            resident,
            body={"rating": 5, "comment": "处理及时"},
            idem=_idem("repair-review"),
            expected_status=201,
        )
        repair_timeline = api.request("GET", f"/api/work-orders/{repair_id}/timeline", resident)
        assert repair["status"] == "CLOSED" and repair["has_review"] is True

        # Billing: real bill/rule read and consultation lifecycle. No payment mutation.
        bills = api.request("GET", "/api/billing/bills", resident, house=True)
        assert bills
        bill_id = str(bills[0]["bill_id"])
        bill_detail = api.request("GET", f"/api/billing/bills/{bill_id}", resident, house=True)
        bill_financial_snapshot = {
            key: bill_detail["bill"][key]
            for key in (
                "property_fee",
                "utility_fee",
                "parking_fee",
                "late_fee",
                "total_amount",
            )
        }
        consultation_parameters = {
            "subject": "E2E 费用咨询",
            "description": "请解释本期物业费组成",
            "bill_id": bill_id,
        }
        consultation_token = api.confirmation(
            resident, "CREATE_CONSULTATION", consultation_parameters
        )
        consultation = api.request(
            "POST",
            "/api/billing/consultations",
            resident,
            body={**consultation_parameters, "confirmation_token": consultation_token},
            idem=_idem("billing-consult"),
            house=True,
            expected_status=201,
        )
        consultation_id = str(consultation["id"])
        consultation = api.request(
            "POST",
            f"/api/billing/consultations/{consultation_id}/submit",
            resident,
            body={"expected_version": consultation["version"]},
        )
        consultation = api.request(
            "POST",
            f"/api/billing/consultations/{consultation_id}/process",
            finance,
            body={"expected_version": consultation["version"]},
        )
        consultation = api.request(
            "POST",
            f"/api/billing/consultations/{consultation_id}/answer",
            finance,
            body={
                "answer": "费用由物业费与公共能耗构成，规则版本见账单详情。",
                "expected_version": consultation["version"],
            },
        )
        consultation = api.request(
            "POST",
            f"/api/billing/consultations/{consultation_id}/resolve",
            finance,
            body={"expected_version": consultation["version"]},
        )
        assert consultation["status"] == "RESOLVED"
        bill_after_consultation = api.request(
            "GET", f"/api/billing/bills/{bill_id}", resident, house=True
        )
        assert {
            key: bill_after_consultation["bill"][key] for key in bill_financial_snapshot
        } == bill_financial_snapshot, "财务咨询不得改写账单金额或费用组成"

        # Inspection task lifecycle.
        task = api.request(
            "POST",
            "/api/inspection-tasks",
            manager,
            body={
                "title": "E2E 消防通道巡检",
                "description": "检查通道、照明与灭火器",
                "route_points": ["1栋大厅", "消防通道"],
            },
            idem=_idem("task-create"),
            expected_status=201,
        )
        task_id = str(task["id"])
        task = api.request(
            "POST",
            f"/api/inspection-tasks/{task_id}/actions/assign",
            manager,
            body={"expected_version": task["version"], "assignee_id": guard.actor_id},
            idem=_idem("task-assign"),
        )
        task = api.request(
            "POST",
            f"/api/inspection-tasks/{task_id}/actions/start",
            guard,
            body={"expected_version": task["version"]},
            idem=_idem("task-start"),
        )
        task_record_parameters = {
            "record_type": "POINT_RECORD",
            "point": "消防通道",
            "note": "通道畅通，照明正常",
        }
        task_record_token = api.confirmation(
            guard, "INSPECTION_TASK_SUBMIT_RECORDS", task_record_parameters
        )
        task = api.request(
            "POST",
            f"/api/inspection-tasks/{task_id}/actions/submit-records",
            guard,
            body={
                "expected_version": task["version"],
                **task_record_parameters,
                "confirmation_token": task_record_token,
            },
            idem=_idem("task-record"),
        )
        task = api.request(
            "POST",
            f"/api/inspection-tasks/{task_id}/actions/complete",
            manager,
            body={"expected_version": task["version"]},
            idem=_idem("task-complete"),
        )
        task_timeline = api.request("GET", f"/api/inspection-tasks/{task_id}/timeline", manager)
        assert task["status"] == "COMPLETED"

        # High-risk event: explicit confirmation, duty notification, disposal, grade and review.
        event_confirmation_parameters = {
            "event_type": "PERSONAL_SAFETY",
            "risk_level": "HIGH_RISK",
            "location": "1栋南门",
        }
        event_token = api.confirmation(
            resident, "SECURITY_EVENT_CREATE", event_confirmation_parameters
        )
        event = api.request(
            "POST",
            "/api/security-events",
            resident,
            body={
                **event_confirmation_parameters,
                "description": "E2E：发现可疑人员徘徊",
                "confirmation_token": event_token,
                "report_source": "MANUAL",
            },
            idem=_idem("event-create"),
            expected_status=201,
        )
        event_id = str(event["id"])
        event = api.request(
            "POST",
            f"/api/security-events/{event_id}/actions/assign",
            manager,
            body={"expected_version": event["version"], "assignee_id": guard.actor_id},
            idem=_idem("event-assign"),
        )
        event = api.request(
            "POST",
            f"/api/security-events/{event_id}/actions/submit-disposal",
            guard,
            body={"expected_version": event["version"], "note": "已核查并劝离"},
            idem=_idem("event-disposal"),
        )
        event = api.request(
            "POST",
            f"/api/security-events/{event_id}/actions/grade-confirm",
            manager,
            body={"expected_version": event["version"]},
            idem=_idem("event-grade"),
        )
        event = api.request(
            "POST",
            f"/api/security-events/{event_id}/actions/review-pass",
            manager,
            body={"expected_version": event["version"]},
            idem=_idem("event-review"),
        )
        event_timeline = api.request("GET", f"/api/security-events/{event_id}/timeline", manager)
        assert event["status"] == "CLOSED" and event["grade_confirmed_by"] == manager.actor_id

        # Announcement: draft -> review -> approve -> confirmation-bound publish.
        announcement = api.request(
            "POST",
            "/api/announcements",
            customer_service,
            body={
                "title": "E2E 电梯维护通知",
                "body": "1栋电梯将于明日 10:00-11:00 维护。",
                "category": "MAINTENANCE",
                "audience_condition": {"building_ids": ["1栋"]},
            },
            idem=_idem("announcement-create"),
            expected_status=201,
        )
        announcement_id = str(announcement["id"])
        audience = api.request(
            "GET", f"/api/announcements/{announcement_id}/audience-preview", customer_service
        )
        assert audience["count"] > 0
        announcement = api.request(
            "POST",
            f"/api/announcements/{announcement_id}/submit-review",
            customer_service,
            body={"expected_version": announcement["version"]},
            idem=_idem("announcement-submit"),
        )
        announcement = api.request(
            "POST",
            f"/api/announcements/{announcement_id}/actions/approve",
            manager,
            body={"expected_version": announcement["version"]},
            idem=_idem("announcement-approve"),
        )
        publish_parameters = {
            "announcement_id": announcement_id,
            "expected_version": announcement["version"],
            "action": "PUBLISH",
        }
        publish_token = api.confirmation(manager, "ANNOUNCEMENT_PUBLISH", publish_parameters)
        announcement = api.request(
            "POST",
            f"/api/announcements/{announcement_id}/actions/publish",
            manager,
            body={
                "expected_version": announcement["version"],
                "confirmation_token": publish_token,
            },
            idem=_idem("announcement-publish"),
        )
        resident_announcements = api.request("GET", "/api/announcements", resident)
        assert any(str(item["id"]) == announcement_id for item in resident_announcements["items"])
        assert announcement["status"] == "PUBLISHED"

        messages = api.request("GET", "/api/messages", resident)
        if messages["items"]:
            api.request(
                "POST",
                f"/api/messages/{messages['items'][0]['id']}/read",
                resident,
                idem=_idem("message-read"),
            )
        read_all = api.request(
            "POST", "/api/messages/read-all", resident, idem=_idem("message-read-all")
        )
        dashboard = api.request("GET", "/api/admin/dashboard", manager)

        agent_result = api.request(
            "POST",
            f"/api/agent/conversations/e2e-{uuid4().hex[:12]}/messages",
            resident,
            body={
                "text": "查一下我的账单",
                "house_id": resident.current_house_id,
                "slots": {"query_type": "list"},
            },
        )
        assert agent_result["intent"] == "BILLING" and agent_result["facts"] is not None, (
            agent_result
        )

        return {
            "auth": {
                "single_house_auto_selected": True,
                "multi_house_count": len(multi.house_ids),
                "selected_house": multi.house_ids[1],
            },
            "repair": {
                "id": repair_id,
                "status": repair["status"],
                "version": repair["version"],
                "timeline_count": len(repair_timeline),
                "reviewed": repair["has_review"],
            },
            "billing": {
                "bill_count": len(bills),
                "rule_known": not bill_detail["unknown_rule"],
                "consultation_id": consultation_id,
                "consultation_status": consultation["status"],
                "amount_unchanged_after_consultation": True,
            },
            "inspection": {
                "task_id": task_id,
                "task_status": task["status"],
                "task_timeline_count": len(task_timeline),
                "event_id": event_id,
                "event_status": event["status"],
                "event_timeline_count": len(event_timeline),
                "grade_confirmed": event["grade_confirmed_by"] == manager.actor_id,
            },
            "announcement": {
                "id": announcement_id,
                "status": announcement["status"],
                "audience_count": audience["count"],
                "resident_visible": True,
            },
            "operations": {
                "resident_message_count": messages["total"],
                "read_all_updated": read_all["updated_count"],
                "failed_message_count": dashboard["failed_message_count"],
                "model_gateway": dashboard["integration_health"]["model_gateway"],
            },
            "agent": {
                "intent": agent_result["intent"],
                "facts_count": agent_result["facts"]["count"],
                "error": agent_result["error"],
            },
        }
    finally:
        api.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.base_url)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
