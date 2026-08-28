"""Generate v1 inventory or run an explicitly approved bounded drain action."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from property_agent.agent.approval_authority import configured_approval_authority
from property_agent.agent.v1_drain import (
    build_v1_drain_inventory,
    expire_abandoned_v1,
    parse_drain_policy,
)
from property_agent.config import settings
from property_agent.platform.database import get_session_factory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--inactive-days", type=int, default=30)
    expire = subparsers.add_parser("expire")
    expire.add_argument("policy", type=Path)
    expire.add_argument("--execute", action="store_true")
    expire.add_argument("--confirm-policy-version", default="")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    factory = get_session_factory()
    with factory() as session:
        if args.command == "inventory":
            report = build_v1_drain_inventory(
                session,
                release_sha=settings.release_sha,
                now=now,
                abandoned_after=timedelta(days=args.inactive_days),
            )
            print(json.dumps(asdict(report), sort_keys=True))
            return 0 if report.complete else 2
        policy = parse_drain_policy(json.loads(args.policy.read_text(encoding="utf-8")))
        if args.execute and args.confirm_policy_version != policy.policy_version:
            parser.error("--execute requires exact --confirm-policy-version")
        receipt = expire_abandoned_v1(
            session,
            policy=policy,
            approval_authority=configured_approval_authority(settings),
            now=now,
            dry_run=not args.execute,
        )
        if args.execute:
            session.commit()
        print(json.dumps(asdict(receipt), sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
