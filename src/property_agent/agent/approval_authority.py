"""Server-verifiable approval authority for protected Agent release decisions.

SHA-256 proves payload integrity only.  This module adds the missing authority
proof: an Ed25519 signature verified against a server-owned trust root.  The
private key is never accepted by the application and must remain in the
independent approval system.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

APPROVAL_SIGNATURE_VERSION = "ed25519-v1"


@dataclass(frozen=True, slots=True)
class TrustedApprovalAuthority:
    """Pinned public verification identity; it never contains signing material."""

    authority_id: str
    public_key_base64: str

    @property
    def configured(self) -> bool:
        return bool(self.authority_id and self.public_key_base64)


def configured_approval_authority(settings: object) -> TrustedApprovalAuthority:
    """Build the server-owned trust root from protected deployment configuration."""
    return TrustedApprovalAuthority(
        authority_id=str(getattr(settings, "agent_approval_authority_id", "") or ""),
        public_key_base64=str(
            getattr(settings, "agent_approval_authority_public_key_base64", "") or ""
        ),
    )


def verify_approval_signature(
    payload: bytes,
    *,
    authority_id: str,
    signature_version: str,
    signature_base64: str,
    authority: TrustedApprovalAuthority,
) -> bool:
    """Verify a signature without accepting a manifest-supplied trust root."""
    if not authority.configured or authority_id != authority.authority_id:
        return False
    if signature_version != APPROVAL_SIGNATURE_VERSION:
        return False
    try:
        public_key_bytes = base64.b64decode(authority.public_key_base64, validate=True)
        signature = base64.b64decode(signature_base64, validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature, payload)
    except (ValueError, TypeError, binascii.Error, InvalidSignature):
        return False
    return True


__all__ = [
    "APPROVAL_SIGNATURE_VERSION",
    "TrustedApprovalAuthority",
    "configured_approval_authority",
    "verify_approval_signature",
]
