"""
Platform auth service — JWT token issuance/verification and bcrypt password hashing.

PRD 5.2: PF-01 (Login). Token payload: actor_id, community_id, roles, bound_house_ids.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
from jose import jwt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JWT_SECRET_KEY: str = os.getenv(
    "JWT_SECRET_KEY",
    "dev-secret-change-in-production-32chars-min",
)
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_HOURS: int = int(os.getenv("JWT_EXPIRE_HOURS", "8"))

BCRYPT_ROUNDS: int = int(os.getenv("BCRYPT_ROUNDS", "12"))


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(plain_password: str) -> str:
    """Hash a plain-text password using bcrypt."""
    password_bytes = plain_password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plain-text password against a stored bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        password_hash.encode("utf-8"),
    )


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def create_jwt_token(
    *,
    actor_id: UUID,
    community_id: UUID,
    roles: list[str],
    bound_house_ids: list[UUID],
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT access token.

    Payload includes actor_id, community_id, roles, and bound_house_ids
    as required by PRD 5.2 PF-01. The frontend must not submit or override
    these claims.
    """
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(hours=JWT_EXPIRE_HOURS))

    payload: dict = {
        "sub": str(actor_id),
        "actor_id": str(actor_id),
        "community_id": str(community_id),
        "roles": roles,
        "bound_house_ids": [str(h) for h in bound_house_ids],
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_jwt_token(token: str) -> dict:
    """Decode and validate a JWT access token.

    Returns the decoded payload dict. Raises JWTError if token is invalid,
    expired, or tampered with.
    """
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
