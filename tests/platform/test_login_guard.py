from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from property_agent.platform.adapters.api.routes import login
from property_agent.platform.adapters.api.schemas import LoginRequest
from property_agent.platform.application.login_guard import LoginGuard, LoginLockedError
from property_agent.platform.infrastructure.orm_models import AuditLogModel, LoginAttemptModel


def _guard(session) -> LoginGuard:
    return LoginGuard(
        session,
        failure_limit=5,
        window=timedelta(minutes=15),
        lock_duration=timedelta(minutes=15),
    )


def _request(ip: str = "192.0.2.10") -> Request:
    request = Request(
        {
            "type": "http",
            "headers": [],
            "client": (ip, 43210),
            "method": "POST",
            "path": "/api/auth/login",
        }
    )
    request.state.request_id = "req-login-guard"
    return request


def test_failure_lock_is_persisted_and_expires(session):
    guard = _guard(session)
    now = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)

    for offset in range(5):
        locked = guard.record_failure(
            " Resident1 ",
            "192.0.2.10",
            now=now + timedelta(seconds=offset),
        )
        assert locked == (offset == 4)
    session.commit()

    persisted = session.query(LoginAttemptModel).one()
    assert persisted.username_normalized == "resident1"
    assert persisted.failure_count == 5

    with pytest.raises(LoginLockedError):
        guard.ensure_allowed("resident1", "192.0.2.10", now=now + timedelta(minutes=14))

    guard.ensure_allowed("resident1", "192.0.2.10", now=now + timedelta(minutes=16))
    assert persisted.failure_count == 0
    assert persisted.locked_until is None


def test_success_clears_failure_state(session):
    guard = _guard(session)
    guard.record_failure("resident1", "192.0.2.10")
    guard.record_success("RESIDENT1", "192.0.2.10")
    session.commit()

    assert session.query(LoginAttemptModel).count() == 0


def test_login_failure_and_success_audits_are_committed(session, seed_data):
    with pytest.raises(HTTPException) as failure:
        login(LoginRequest(username="resident1", password="wrong"), _request(), session)
    assert failure.value.status_code == 401
    assert session.query(LoginAttemptModel).count() == 1
    assert session.query(AuditLogModel).filter_by(action="LOGIN_FAILED").count() == 1

    response = login(LoginRequest(username="resident1", password="123456"), _request(), session)
    assert response.actor_id == seed_data["user_a"]
    assert session.query(LoginAttemptModel).count() == 0
    assert session.query(AuditLogModel).filter_by(action="LOGIN_SUCCESS").count() == 1


def test_unknown_user_has_same_failure_message_and_is_locked(session):
    for attempt in range(5):
        with pytest.raises(HTTPException) as failure:
            login(LoginRequest(username="does-not-exist", password="wrong"), _request(), session)
        expected_status = 429 if attempt == 4 else 401
        assert failure.value.status_code == expected_status
        if expected_status == 401:
            assert failure.value.detail == "Invalid username or password"

    with pytest.raises(HTTPException) as blocked:
        login(LoginRequest(username="does-not-exist", password="wrong"), _request(), session)
    assert blocked.value.status_code == 429
    assert blocked.value.detail == "Too many login attempts. Please try again later."
