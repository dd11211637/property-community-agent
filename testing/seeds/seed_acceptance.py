"""Provision deterministic records only for the isolated RC acceptance profile."""

from property_agent.config import settings
from testing.seeds.seed_demo import seed


def main() -> int:
    if settings.deployment_environment != "isolated-test":
        raise RuntimeError("Acceptance fixtures require DEPLOYMENT_ENVIRONMENT=isolated-test")
    seed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
