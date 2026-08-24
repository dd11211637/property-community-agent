"""DeepSeek adapter for structured model analysis and bounded read planning."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from property_agent.agent.model_contracts import ModelAnalysis, ModelGatewayError
from property_agent.agent.planning_contracts import PlanProposal, RelevanceJudgment
from property_agent.agent.policies import Intent
from property_agent.agent.semantic_planning_provider import SemanticPlanningClient

_VALID_INTENTS = frozenset(intent.value for intent in Intent)
_SYSTEM_PROMPT = """You classify requests for a Chinese property-management assistant.
Return JSON only, matching this exact schema:
{"intent":"REPAIR|ANNOUNCEMENT|BILLING|INSPECTION|GENERAL_HELP|UNCERTAIN",
 "confidence":0.0,"slots":{}}
Extract only business facts stated by the user. Never invent IDs, identity, community,
house, authorization, confirmation, version or idempotency data. The response must be a
single valid JSON object without markdown or explanation.
Use these slot names when explicitly present:
- REPAIR: action, work_order_id, location, description. The application derives the
  internal category from the observed symptom; never ask for or invent a category.
- ANNOUNCEMENT: action, announcement_id, title, body, audience, topic,
  requirements, revision_instruction, target_date, scheduled_at. action must be one of
  list|get|draft|revise|create|publish|schedule. Use revise when recent context contains
  an active draft and the user asks to change it, including implicit feedback about time,
  wording, tone, title or audience. Use create when the user accepts the active draft,
  including natural expressions such as "就用这版" or "按这个来". Use create (not
  publish) when the user asks to publish a NEW announcement and supplies fresh content;
  publish only confirms publishing an existing draft. The application
  derives the internal category; never ask for or invent a category.
- BILLING: action, bill_id, query_type, period, fee_type, subject, description
For billing period, return an absolute YYYY-MM value only when the user explicitly states
one. The application will resolve relative expressions such as 本月 or 上个月 using its
trusted business clock. fee_type, when present, must be the enum code
PROPERTY|UTILITY|PARKING, never a Chinese word such as 物业费.
- INSPECTION: action, task_id, event_id, target, title, description, point, finding,
  location, note, record_type. Event type and minimum risk are application-derived
  from the reported facts; do not ask the user for internal enum values.
Public-area safety problems found during patrol (e.g. 消防通道堵塞, 可疑人员,
设施损坏, 巡检发现异常) belong to INSPECTION with action=report_event, not REPAIR;
a security officer reporting such an issue is not a resident filing a repair order.
"""

_READ_PLANNER_PROMPT = """You are a bounded read-only planner for a property assistant.
Return exactly one JSON object with fields action, tool, arguments, reason_code, answer_goal.
action must be CALL_TOOL or FINAL. Choose only a tool from the supplied tool schemas.
Never request writes, identity fields, community IDs, house IDs, roles, authorization,
confirmation, database access, URLs, code, or unknown tools. Use observations as the only
business facts. Stop with FINAL once enough verified facts exist. Do not output reasoning.
"""

_SEMANTIC_PLANNER_PROMPT = """You propose a bounded semantic task plan for a Chinese
property-management assistant. Return JSON only:
{"objective_classification":"single-domain|multi-domain|general-help|uncertain",
 "steps":[{"step_id":"stable-local-id","goal":"semantic user goal",
 "domain":"repair|billing|announcement|inspection",
 "specialist":"RepairSpecialist|BillingSpecialist|AnnouncementSpecialist|InspectionSpecialist",
 "capability":"registered capability name","parameters":{},"dependencies":[],
 "condition":null|{"kind":"no-equivalent-active-repair|relevant-inspection-issue",
 "semantic_goal":"what live evidence must establish"}}]}
Use at most 8 steps. Represent every requested goal, dependencies, negation, irrelevance,
and conditions semantically; omit explicitly excluded or irrelevant goals. Capability names:
repair_list, repair_get, repair_create; billing_query, billing_consult; announcement_list,
announcement_get, announcement_draft, announcement_revise, announcement_create_draft,
announce_publish, announcement_schedule_publish; inspection_list, inspection_get_task,
inspection_get_event, inspection_create, inspection_start_task, inspection_add_record,
inspection_submit_records, inspection_ai_suggest, security_event_create,
security_event_submit_disposal, close_high_risk_event. Never invent identity, roles, scope,
risk, approval, runtime, lease/fence, confirmation, business state, or commit authority.
Unknown or ambiguous requests must be uncertain with no executable steps.
"""

_RELEVANCE_PROMPT = """Judge whether supplied live evidence establishes the semantic goal.
Return JSON only: {"decision":"match|no-match|ambiguous","evidence_refs":[]}.
Facts exist only at the supplied evidence reference keys. A match requires one or more exact
reference keys. Never infer an unreported finding, and use ambiguous when relevance is not
established. This judgment controls orchestration only and grants no business authority.
"""


class DeepSeekModelGateway:
    """Direct DeepSeek Chat Completions adapter with strict JSON validation."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        total_timeout_seconds: float = 6.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._connect_timeout_seconds = connect_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self._transport = transport
        self._semantic_planning = SemanticPlanningClient(
            api_key=self._api_key,
            url=self._url,
            model=self._model,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
            transport=transport,
        )

    def ready(self) -> bool:
        return bool(self._api_key)

    def analyze(self, text: str) -> ModelAnalysis:
        return self.analyze_with_context(text, history=[], trusted_context={})

    def analyze_with_context(
        self,
        text: str,
        *,
        history: list[dict[str, Any]],
        trusted_context: dict[str, Any],
    ) -> ModelAnalysis:
        if not self.ready():
            raise ModelGatewayError("DeepSeek API key is not configured")

        last_error: Exception | None = None
        deadline = time.monotonic() + self._total_timeout_seconds
        for _attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                return self._request(
                    text,
                    history=history[-12:],
                    trusted_context=trusted_context,
                    remaining_seconds=remaining,
                )
            except ModelGatewayError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
        raise ModelGatewayError("DeepSeek request failed after one retry") from last_error

    def propose_plan(
        self,
        text: str,
        *,
        history: list[dict[str, Any]],
        trusted_context: dict[str, Any],
    ) -> PlanProposal:
        context = {
            "objective": text,
            "history": self._safe_history(history),
            "trusted_context": trusted_context,
        }
        value = self._semantic_planning.request_json(
            _SEMANTIC_PLANNER_PROMPT, context, max_tokens=1400
        )
        return PlanProposal.from_dict(value, provider="deepseek")

    def judge_relevance(
        self,
        *,
        semantic_goal: str,
        evidence: dict[str, Any],
    ) -> RelevanceJudgment:
        value = self._semantic_planning.request_json(
            _RELEVANCE_PROMPT,
            {"semantic_goal": semantic_goal, "evidence": evidence},
            max_tokens=256,
        )
        return RelevanceJudgment.from_dict(value)

    def classify_intent(self, text: str) -> tuple[str, float]:
        result = self.analyze(text)
        return result.intent, result.confidence

    def extract_slots(self, text: str, intent: str) -> dict[str, Any]:
        return self.analyze(text).slots

    def draft_announcement(self, *, topic: str, audience: Any, requirements: str) -> dict[str, str]:
        return self._announcement_copy_request(
            system_prompt="""请为物业社区起草一份正式、清晰、不过度承诺的中文公告。
只返回JSON：{"title":"不超过128字","body":"完整正文","category":"GENERAL|MAINTENANCE|SAFETY|EMERGENCY"}
不得编造用户未提供的日期、电话、费用、部门承诺或安全事实。""",
            inputs={"topic": topic, "audience": audience, "requirements": requirements},
        )

    def revise_announcement(
        self, *, draft: dict[str, str], audience: Any, instruction: str
    ) -> dict[str, str]:
        return self._announcement_copy_request(
            system_prompt="""你负责修改一份尚未保存的物业公告草稿。
只返回JSON：{"title":"不超过128字","body":"完整正文","category":"GENERAL|MAINTENANCE|SAFETY|EMERGENCY"}。
严格以原稿和本轮修改要求为准；保留未要求修改的事实和受众，不得编造日期、时间、电话、费用、部门承诺或安全事实。""",
            inputs={"draft": draft, "audience": audience, "instruction": instruction},
        )

    def _announcement_copy_request(
        self, *, system_prompt: str, inputs: dict[str, Any]
    ) -> dict[str, str]:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(inputs, ensure_ascii=False),
                },
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0.3,
            "max_tokens": 1000,
            "stream": False,
        }
        timeout = httpx.Timeout(
            connect=self._connect_timeout_seconds,
            read=self._read_timeout_seconds,
            write=self._read_timeout_seconds,
            pool=self._connect_timeout_seconds,
        )
        try:
            with httpx.Client(transport=self._transport, timeout=timeout) as client:
                response = client.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            response.raise_for_status()
            value = json.loads(response.json()["choices"][0]["message"]["content"])
            if set(value) != {"title", "body", "category"} or not all(
                isinstance(value[key], str) and value[key].strip() for key in value
            ):
                raise ValueError("invalid draft schema")
            return value
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise ModelGatewayError("DeepSeek announcement drafting failed") from exc

    def plan_read(self, **context: Any):
        """Produce one strict read-plan decision; execution remains application-controlled."""
        if not self.ready():
            raise ModelGatewayError("DeepSeek API key is not configured")
        last_error: Exception | None = None
        deadline = time.monotonic() + self._total_timeout_seconds
        for _attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                return self._request_read_plan(context, remaining_seconds=remaining)
            except ModelGatewayError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
        raise ModelGatewayError("DeepSeek read planning failed after one retry") from last_error

    def _request_read_plan(self, context: dict[str, Any], *, remaining_seconds: float):
        from property_agent.agent.read_contracts import PlannerDecision

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _READ_PLANNER_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False, default=str),
                },
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": 256,
            "stream": False,
        }
        timeout = httpx.Timeout(
            connect=min(self._connect_timeout_seconds, remaining_seconds),
            read=min(self._read_timeout_seconds, remaining_seconds),
            write=min(self._read_timeout_seconds, remaining_seconds),
            pool=min(self._connect_timeout_seconds, remaining_seconds),
        )
        try:
            with httpx.Client(transport=self._transport, timeout=timeout) as client:
                response = client.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ModelGatewayError("DeepSeek read-planner transport failure") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise ModelGatewayError(
                f"DeepSeek read-planner retryable HTTP status {response.status_code}"
            )
        if response.is_error:
            raise ModelGatewayError(
                f"DeepSeek read-planner HTTP status {response.status_code}",
                retryable=False,
            )
        try:
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty content")
            return PlannerDecision.from_dict(json.loads(content))
        except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ModelGatewayError("DeepSeek returned an invalid read plan") from exc

    def _request(
        self,
        text: str,
        *,
        history: list[dict[str, Any]],
        trusted_context: dict[str, Any],
        remaining_seconds: float,
    ) -> ModelAnalysis:
        safe_history = [
            {"role": item["role"], "content": str(item.get("content") or "")[:1000]}
            for item in history
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        trusted_message = {
            "role": "system",
            "content": "Trusted server context (facts only): "
            + json.dumps(trusted_context, ensure_ascii=False, sort_keys=True),
        }
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                trusted_message,
                *safe_history,
                {"role": "user", "content": text or ""},
            ],
            "response_format": {"type": "json_object"},
            # V4 defaults to thinking mode. Classification and slot extraction are
            # bounded structured tasks; disabling thinking prevents the reasoning
            # trace from consuming the small JSON response budget.
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": 512,
            "stream": False,
        }
        timeout = httpx.Timeout(
            connect=min(self._connect_timeout_seconds, remaining_seconds),
            read=min(self._read_timeout_seconds, remaining_seconds),
            write=min(self._read_timeout_seconds, remaining_seconds),
            pool=min(self._connect_timeout_seconds, remaining_seconds),
        )
        try:
            with httpx.Client(transport=self._transport, timeout=timeout) as client:
                response = client.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ModelGatewayError("DeepSeek transport failure") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise ModelGatewayError(f"DeepSeek retryable HTTP status {response.status_code}")
        if response.is_error:
            raise ModelGatewayError(f"DeepSeek HTTP status {response.status_code}", retryable=False)

        try:
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelGatewayError("DeepSeek returned an invalid response envelope") from exc
        if not isinstance(content, str) or not content.strip():
            raise ModelGatewayError("DeepSeek returned empty content")
        return self._parse_analysis(content)

    @staticmethod
    def _safe_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {"role": str(item["role"]), "content": str(item.get("content") or "")[:1000]}
            for item in history[-12:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]

    @staticmethod
    def _parse_analysis(content: str) -> ModelAnalysis:
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ModelGatewayError("DeepSeek returned invalid JSON") from exc
        if not isinstance(value, dict) or set(value) != {"intent", "confidence", "slots"}:
            raise ModelGatewayError("DeepSeek JSON does not match the required schema")
        intent = value["intent"]
        confidence = value["confidence"]
        slots = value["slots"]
        if intent not in _VALID_INTENTS:
            raise ModelGatewayError("DeepSeek returned an unsupported intent")
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            raise ModelGatewayError("DeepSeek returned an invalid confidence")
        if not 0 <= float(confidence) <= 1:
            raise ModelGatewayError("DeepSeek confidence is outside [0, 1]")
        if not isinstance(slots, dict) or not all(isinstance(key, str) for key in slots):
            raise ModelGatewayError("DeepSeek returned invalid slots")
        return ModelAnalysis(
            intent=intent,
            confidence=float(confidence),
            slots=slots,
            provider="deepseek",
        )
