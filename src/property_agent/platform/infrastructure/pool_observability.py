"""Low-cardinality SQLAlchemy pool lifecycle observation.

This module observes the existing pool.  It neither configures nor owns connections.
"""

from __future__ import annotations

from threading import Lock
from time import perf_counter
from typing import Any

from sqlalchemy import Engine, event


class DatabasePoolObserver:
    """Project pool events into the application-owned observability provider."""

    def __init__(self, engine: Engine, observability: Any) -> None:
        self._pool = engine.pool
        self._telemetry = observability
        self._checkout_started: dict[int, float] = {}
        self._lock = Lock()
        self._checkouts = 0
        self._checkins = 0
        self._timeouts = 0
        self._failures = 0
        self._peak_in_use = 0
        self._peak_overflow = 0

    def install(self) -> None:
        self._pool._property_agent_observer = self
        event.listen(self._pool, "checkout", self.checkout)
        event.listen(self._pool, "checkin", self.checkin)
        event.listen(self._pool, "invalidate", self.invalidate)
        event.listen(self._pool, "soft_invalidate", self.soft_invalidate)
        self._record_levels()

    def use_observability(self, observability: Any) -> None:
        self._telemetry = observability
        self._record_levels()

    def checkout(self, _dbapi_connection: Any, connection_record: Any, _proxy: Any) -> None:
        with self._lock:
            self._checkout_started[id(connection_record)] = perf_counter()
            self._checkouts += 1
        self._telemetry.count("database_pool_checkout_total", attributes={"outcome": "success"})
        self._record_levels()

    def checkin(self, _dbapi_connection: Any, connection_record: Any) -> None:
        with self._lock:
            started = self._checkout_started.pop(id(connection_record), None)
            self._checkins += 1
        self._telemetry.count("database_pool_checkin_total", attributes={"outcome": "success"})
        if started is not None:
            self._telemetry.duration(
                "database_pool_connection_use_duration_seconds", perf_counter() - started
            )
        self._record_levels()

    def invalidate(self, *_args: Any) -> None:
        with self._lock:
            self._failures += 1
        self._telemetry.count(
            "database_pool_connection_failure_total",
            attributes={"outcome": "failure", "reason": "invalidate"},
        )

    def soft_invalidate(self, *_args: Any) -> None:
        with self._lock:
            self._failures += 1
        self._telemetry.count(
            "database_pool_connection_failure_total",
            attributes={"outcome": "failure", "reason": "soft_invalidate"},
        )

    def timeout(self) -> None:
        with self._lock:
            self._timeouts += 1
        self._telemetry.count(
            "database_pool_checkout_total",
            attributes={"outcome": "failure", "reason": "timeout"},
        )

    def _record_levels(self) -> None:
        in_use = _pool_value(self._pool, "checkedout")
        overflow = _pool_value(self._pool, "overflow")
        with self._lock:
            self._peak_in_use = max(self._peak_in_use, in_use or 0)
            self._peak_overflow = max(self._peak_overflow, overflow or 0)
        for metric, method in (
            ("database_pool_connections_in_use", "checkedout"),
            ("database_pool_connections_idle", "checkedin"),
            ("database_pool_base_capacity", "size"),
            ("database_pool_current_overflow", "overflow"),
        ):
            value = _pool_value(self._pool, method)
            if value is not None:
                self._telemetry.value(metric, max(0, value))
        overflow_allowance = _pool_attribute(self._pool, "_max_overflow")
        if overflow_allowance is not None:
            self._telemetry.value("database_pool_overflow_allowance", overflow_allowance)

    def snapshot(self) -> dict[str, int | str]:
        """Return bounded operational counters without connection or request identity."""
        with self._lock:
            counters = {
                "checkout_total": self._checkouts,
                "checkin_total": self._checkins,
                "timeout_total": self._timeouts,
                "failure_total": self._failures,
                "peak_in_use": self._peak_in_use,
                "peak_overflow": self._peak_overflow,
            }
        snapshot = {
            "state": "OBSERVED",
            **counters,
            "current_in_use": max(0, _pool_value(self._pool, "checkedout") or 0),
            "current_idle": max(0, _pool_value(self._pool, "checkedin") or 0),
            "base_capacity": max(0, _pool_value(self._pool, "size") or 0),
            "current_overflow": max(0, _pool_value(self._pool, "overflow") or 0),
        }
        overflow_allowance = _pool_attribute(self._pool, "_max_overflow")
        if overflow_allowance is not None:
            snapshot["overflow_allowance"] = overflow_allowance
        return snapshot


def install_pool_observability(engine: Engine, observability: Any) -> DatabasePoolObserver:
    existing = getattr(engine.pool, "_property_agent_observer", None)
    if isinstance(existing, DatabasePoolObserver):
        existing.use_observability(observability)
        return existing
    observer = DatabasePoolObserver(engine, observability)
    observer.install()
    return observer


def _pool_value(pool: Any, method_name: str) -> int | None:
    method = getattr(pool, method_name, None)
    if not callable(method):
        return None
    try:
        return int(method())
    except (NotImplementedError, TypeError, ValueError):
        return None


def _pool_attribute(pool: Any, attribute_name: str) -> int | None:
    value = getattr(pool, attribute_name, None)
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
