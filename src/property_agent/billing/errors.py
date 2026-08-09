"""
billing/errors.py     业务错误定义（PRD 6.3）

所有错误继承 platform.errors.BusinessError，统一经 error_envelope 渲染。
"""

from __future__ import annotations

from property_agent.platform.errors import BusinessError


class BillingError(BusinessError):
    """账单业务错误。"""

    def __init__(self, code: str, message: str, status_code: int = 400, details=None):
        super().__init__(code, message, status_code, details)


class ConsultationError(BusinessError):
    """财务咨询单业务错误。"""

    def __init__(self, code: str, message: str, status_code: int = 400, details=None):
        super().__init__(code, message, status_code, details)


class BillingSourceUnavailable(Exception):
    """账单数据源不可用（PRD R-02：接口中断时不猜测金额）。

    调用方捕获后应允许保存财务咨询草稿，而非返回伪造金额。
    """
