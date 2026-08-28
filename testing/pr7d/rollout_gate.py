"""Validate one signed rollout-stage evidence artifact without changing rollout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from property_agent.agent.approval_authority import configured_approval_authority
from property_agent.agent.model_release import actual_model_release_identity
from property_agent.agent.rollout_evidence import evaluate_promotion_gate, parse_rollout_evidence
from property_agent.config import settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.evidence.read_text(encoding="utf-8"))
        evidence = parse_rollout_evidence(document)
        decision = evaluate_promotion_gate(
            evidence,
            actual_model_release=actual_model_release_identity(),
            approval_authority=configured_approval_authority(settings),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "reasons": [str(exc)]}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "schema_version": evidence.schema_version,
                "stage": decision.stage.value,
                "status": decision.status.value,
                "reasons": list(decision.reasons),
            },
            sort_keys=True,
        )
    )
    return 0 if decision.status.value == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
