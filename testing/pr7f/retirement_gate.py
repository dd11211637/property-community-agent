"""Evaluate PR7-F static and combined retirement readiness without deleting v1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from property_agent.agent.approval_authority import configured_approval_authority
from property_agent.agent.retirement_gate import (
    DynamicZeroEvidence,
    RetirementApproval,
    RetirementEvidence,
    evaluate_retirement_gate,
    scan_static_v1_dependencies,
)
from property_agent.agent.rollout_evidence import (
    EvidenceStatus,
    PromotionGateDecision,
    RolloutStage,
)
from property_agent.agent.v1_drain import V1DrainInventory
from property_agent.config import settings

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("static")
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    static_report = scan_static_v1_dependencies(ROOT / "src")
    if args.command == "static":
        print(_static_json(static_report))
        return 0 if static_report.passed else 2
    try:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        evidence = _parse_bundle(bundle, static_report)
        decision = evaluate_retirement_gate(
            evidence,
            approval_authority=configured_approval_authority(settings),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "reasons": [str(exc)]}, sort_keys=True))
        return 1
    print(json.dumps({"status": decision.status.value, "reasons": decision.reasons}))
    return 0 if not decision.reasons else 2


def _parse_bundle(data: dict, static_report) -> RetirementEvidence:
    r5 = data.get("r5_decision", {})
    dynamic = data.get("dynamic_zero", {})
    inventory = data.get("drain_inventory", {})
    approval = data.get("retirement_approval", {})
    return RetirementEvidence(
        schema_version=str(data.get("schema_version", "")),
        release_sha=str(data.get("release_sha", "")),
        r5_decision=PromotionGateDecision(
            EvidenceStatus(str(r5.get("status", "PENDING"))),
            RolloutStage(str(r5.get("stage", "R5"))),
            tuple(r5.get("reasons", ())),
        ),
        static_interlock=static_report,
        dynamic_zero=DynamicZeroEvidence(**dynamic),
        drain_inventory=V1DrainInventory(**inventory),
        retirement_approval=RetirementApproval(**approval),
        runtime_switch_violation_count=int(data.get("runtime_switch_violation_count", -1)),
        unresolved_blocker_count=int(data.get("unresolved_blocker_count", -1)),
        rollback_exercised=bool(data.get("rollback_exercised", False)),
    )


def _static_json(report) -> str:
    return json.dumps(
        {
            "scanner_version": report.scanner_version,
            "status": "PASS" if report.passed else "PENDING",
            "dependencies": [
                {
                    "path": item.path,
                    "line": item.line,
                    "kind": item.kind,
                    "value": item.value,
                }
                for item in report.dependencies
            ],
        },
        sort_keys=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
