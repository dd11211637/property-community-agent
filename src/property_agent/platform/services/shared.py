"""
Platform services — backward-compatible re-exports.

PRD 5.3: PF-04 (Idempotency & Confirmation), PF-05 (Message Outbox), PF-06 (Audit).

The canonical implementations now live in:
- application/idempotency_service.py
- application/confirmation_service.py
- application/audit_service.py
- infrastructure/outbox_dispatcher.py

This module re-exports for backward compatibility with existing code
that imports from property_agent.platform.services.shared.
"""

from __future__ import annotations

from property_agent.platform.application.audit_service import (  # noqa: F401
    SENSITIVE_FIELDS,
    AuditService,
    DataMasker,
    audit_log,
)
from property_agent.platform.application.confirmation_service import (  # noqa: F401
    CONFIRMATION_TTL_MINUTES,
    ConfirmationService,
)

# Re-export from application layer
from property_agent.platform.application.idempotency_service import (  # noqa: F401
    IdempotencyService,
    _hash_dict,
)

# Re-export from infrastructure layer
from property_agent.platform.infrastructure.outbox_dispatcher import (  # noqa: F401
    MAX_RETRY_COUNT,
    MessageOutboxService,
    OutboxDispatcher,
)


# Backward-compatible _mask_sensitive alias
def _mask_sensitive(data: dict) -> dict:
    """Backward-compatible wrapper for DataMasker.mask_sensitive_data."""
    result = DataMasker.mask_sensitive_data(data)
    return result if result is not None else {}
