"""Sign the canonical baseline approval with an external Ed25519 private key."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from property_agent.agent.approval_authority import APPROVAL_SIGNATURE_VERSION
from property_agent.agent.model_release_approval import (
    BASELINE_APPROVAL_MANIFEST_VERSION,
    baseline_approval_signature_payload,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "config/pr7b_real_model_baseline_approval.json"
ARTIFACT_PATH = "config/pr7b_real_model_approved_baseline_v1.json"


def prepare_signed_manifest(
    root: Path,
    manifest_path: Path,
    private_key_path: Path,
    authority_id: str,
) -> dict[str, Any]:
    """Validate the candidate identity and return a signed APPROVED manifest."""
    resolved_root = root.resolve()
    expected_manifest = (resolved_root / "config/pr7b_real_model_baseline_approval.json").resolve()
    if manifest_path.resolve() != expected_manifest:
        raise ValueError("only the canonical baseline approval manifest may be signed")
    if private_key_path.resolve().is_relative_to(resolved_root):
        raise ValueError("approval private key must remain outside the repository")

    approval = json.loads(expected_manifest.read_text(encoding="utf-8"))
    if approval.get("approval_manifest_version") != BASELINE_APPROVAL_MANIFEST_VERSION:
        raise ValueError("unsupported approval manifest version")
    if approval.get("approval_status") != "PENDING":
        raise ValueError("approval manifest must be PENDING before independent signing")
    if approval.get("artifact_path") != ARTIFACT_PATH:
        raise ValueError("approval manifest does not name the canonical baseline artifact")

    artifact = (resolved_root / ARTIFACT_PATH).resolve()
    expected_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if approval.get("artifact_sha256") != expected_digest:
        raise ValueError("candidate baseline digest does not match approval manifest")
    if not authority_id.strip():
        raise ValueError("approval authority ID is required")

    private_key = serialization.load_pem_private_key(
        private_key_path.read_bytes(),
        password=None,
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("approval private key must be Ed25519 PEM")
    signed = {
        **approval,
        "approval_status": "APPROVED",
        "approval_authority_id": authority_id.strip(),
        "approval_signature_version": APPROVAL_SIGNATURE_VERSION,
    }
    signature = private_key.sign(baseline_approval_signature_payload(signed))
    signed["approval_signature"] = base64.b64encode(signature).decode("ascii")
    return signed


def write_signed_manifest(path: Path, approval: dict[str, Any]) -> None:
    """Write canonical JSON bytes only after a real signature has been produced."""
    payload = json.dumps(approval, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_bytes(payload.encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--private-key-file", type=Path, required=True)
    parser.add_argument("--authority-id", required=True)
    args = parser.parse_args()
    approval = prepare_signed_manifest(
        ROOT,
        args.manifest,
        args.private_key_file,
        args.authority_id,
    )
    write_signed_manifest(args.manifest, approval)
    print("BASELINE_APPROVAL_SIGNATURE=WRITTEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
