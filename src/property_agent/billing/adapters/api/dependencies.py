"""
adapters/api/dependencies.py     账单模块 API 依赖（PRD 6.3）

- ``get_billing_service`` / ``get_consultation_service``：从 app.state 取装配好的
  生产服务；未装配时返回 503 ADAPTER_NOT_CONFIGURED（与 repair/announcement 一致）。
- 鉴权沿用平台 ``get_request_context`` 接缝（生产环境经 JWT 绑定）。
"""

from __future__ import annotations

from fastapi import Request

from property_agent.billing.application.service import (
    BillingService,
    ConsultationService,
)
from property_agent.billing.errors import BillingError


def get_billing_service(request: Request) -> BillingService:
    service = getattr(request.app.state, "billing_service", None)
    if not isinstance(service, BillingService):
        raise BillingError("ADAPTER_NOT_CONFIGURED", "账单服务尚未配置", 503)
    return service


def get_consultation_service(request: Request) -> ConsultationService:
    service = getattr(request.app.state, "consultation_service", None)
    if not isinstance(service, ConsultationService):
        raise BillingError("ADAPTER_NOT_CONFIGURED", "财务咨询服务尚未配置", 503)
    return service
