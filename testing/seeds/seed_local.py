"""Provision starter identities and records for explicit localhost use only."""

from property_agent.config import settings
from testing.seeds.seed_demo import seed


def main() -> int:
    """Seed the persistent RC database only under the local-use environment."""
    if settings.deployment_environment != "local-use":
        raise RuntimeError("Local bootstrap requires DEPLOYMENT_ENVIRONMENT=local-use")
    seed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
