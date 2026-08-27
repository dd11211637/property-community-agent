"""Safe, exact-SHA certification evidence shared by focused PR7-B gates."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


_FORBIDDEN_KEYS = frozenset(
    {
        "raw_prompt",
        "prompt_text",
        "raw_text",
        "raw_content",
        "raw_memory",
        "api_key",
        "credential",
        "confirmation_token",
        "approval_ref",
        "idempotency_key",
        "phone",
        "address",
        "email",
    }
)


@dataclass(frozen=True, slots=True)
class GateEvidence:
    schema_version: str
    gate: str
    status: GateStatus
    release_sha: str
    git_dirty: bool
    environment: str
    started_at: str
    ended_at: str
    dataset_version: str = ""
    dataset_sha256: str = ""
    configuration: dict[str, Any] = field(default_factory=dict)
    sample_counts: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, float | int | str | bool | None] = field(default_factory=dict)
    hard_gates: dict[str, bool] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        _reject_sensitive(value)
        return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def repository_state(root: Path, requested_sha: str | None = None) -> tuple[str, bool]:
    sha = _git(root, "rev-parse", "HEAD")
    if requested_sha:
        resolved = _git(root, "rev-parse", f"{requested_sha}^{{commit}}")
        if resolved != sha:
            raise RuntimeError(f"requested SHA {resolved} does not match checkout HEAD {sha}")
    dirty = bool(_git(root, "status", "--porcelain=v1", "--untracked-files=no"))
    return sha, dirty


def dataset_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_evidence(path: Path, evidence: GateEvidence) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8")


def validate_safe_payload(value: Any) -> None:
    """Reject fields that could turn aggregate certification into a data channel."""
    _reject_sensitive(value)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return completed.stdout.strip()


def _reject_sensitive(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            if normalized in _FORBIDDEN_KEYS or normalized.endswith("_api_key"):
                raise ValueError(f"sensitive evidence field is forbidden: {path}.{key}")
            _reject_sensitive(nested, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, nested in enumerate(value):
            _reject_sensitive(nested, f"{path}[{index}]")
