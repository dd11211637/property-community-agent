from typing import Any

from property_agent.announcement.domain.errors import validation_error

ALLOWED_CATEGORIES = frozenset({"GENERAL", "MAINTENANCE", "SAFETY", "EMERGENCY"})
HIGH_RISK_CATEGORIES = frozenset({"SAFETY", "EMERGENCY"})
AUDIENCE_FIELDS = frozenset({"building_ids", "unit_ids", "house_types"})


def normalize_audience_condition(value: dict[str, Any] | None) -> dict[str, list[str]]:
    condition = value or {}
    if not isinstance(condition, dict) or set(condition) - AUDIENCE_FIELDS:
        raise validation_error("Audience condition contains unsupported fields.")
    normalized: dict[str, list[str]] = {}
    for field, items in condition.items():
        if not isinstance(items, list) or not items:
            raise validation_error(f"Audience condition field {field} must be a non-empty list.")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 64 for item in items):
            raise validation_error(f"Audience condition field {field} contains an invalid value.")
        normalized[field] = sorted(set(item.strip() for item in items))
    return normalized


def validate_category(category: str) -> str:
    candidate = category.strip().upper()
    if candidate not in ALLOWED_CATEGORIES:
        raise validation_error("Announcement category is not supported.")
    return candidate
