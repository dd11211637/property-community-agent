"""Persistent brute-force protection for the authentication endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from property_agent.platform.infrastructure.orm_models import LoginAttemptModel


class LoginLockedError(Exception):
    """Raised when a username/source pair is temporarily locked."""


class LoginGuard:
    def __init__(
        self,
        session: Session,
        *,
        failure_limit: int,
        window: timedelta,
        lock_duration: timedelta,
    ) -> None:
        if failure_limit <= 0 or window <= timedelta(0) or lock_duration <= timedelta(0):
            raise ValueError("Login guard limits and durations must be positive")
        self._session = session
        self._failure_limit = failure_limit
        self._window = window
        self._lock_duration = lock_duration

    @staticmethod
    def normalize_username(username: str) -> str:
        return username.strip().casefold()

    def ensure_allowed(
        self,
        username: str,
        source_ip: str,
        *,
        now: datetime | None = None,
    ) -> None:
        current = now or datetime.now(timezone.utc)
        attempt = self._find_for_update(self.normalize_username(username), source_ip)
        if attempt is None or attempt.locked_until is None:
            return
        if self._as_utc(attempt.locked_until) > current:
            raise LoginLockedError
        attempt.failure_count = 0
        attempt.window_started_at = current
        attempt.locked_until = None

    def record_failure(
        self,
        username: str,
        source_ip: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(timezone.utc)
        normalized = self.normalize_username(username)
        attempt = self._find_for_update(normalized, source_ip)
        if attempt is None:
            attempt = self._create_or_reload(normalized, source_ip, current)

        window_started = self._as_utc(attempt.window_started_at)
        if current - window_started >= self._window:
            attempt.failure_count = 1
            attempt.window_started_at = current
            attempt.locked_until = None
        else:
            attempt.failure_count += 1

        if attempt.failure_count >= self._failure_limit:
            attempt.locked_until = current + self._lock_duration
            return True
        return False

    def record_success(self, username: str, source_ip: str) -> None:
        attempt = self._find_for_update(self.normalize_username(username), source_ip)
        if attempt is not None:
            self._session.delete(attempt)

    def _find_for_update(self, username: str, source_ip: str) -> LoginAttemptModel | None:
        return (
            self._session.query(LoginAttemptModel)
            .filter_by(username_normalized=username, source_ip=source_ip)
            .with_for_update()
            .one_or_none()
        )

    def _create_or_reload(
        self,
        username: str,
        source_ip: str,
        current: datetime,
    ) -> LoginAttemptModel:
        attempt = LoginAttemptModel(
            username_normalized=username,
            source_ip=source_ip,
            failure_count=0,
            window_started_at=current,
        )
        try:
            with self._session.begin_nested():
                self._session.add(attempt)
                self._session.flush()
            return attempt
        except IntegrityError:
            # Another replica inserted the same pair. The savepoint rollback
            # leaves the outer login/audit transaction usable.
            existing = self._find_for_update(username, source_ip)
            if existing is None:  # pragma: no cover - defensive DB failure
                raise
            return existing

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
