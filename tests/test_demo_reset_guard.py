import pytest

from testing.reset.reset_demo import validate_target


def test_demo_reset_accepts_only_confirmed_demo_database() -> None:
    validate_target("postgresql+psycopg://user:password@postgres/property_agent_demo", "demo", True)


@pytest.mark.parametrize(
    ("url", "environment", "confirmed"),
    [
        ("postgresql+psycopg://user:password@postgres/property_agent", "demo", True),
        ("postgresql+psycopg://user:password@remote/property_agent_demo", "demo", True),
        ("postgresql+psycopg://user:password@postgres/property_agent_demo", "production", True),
        ("postgresql+psycopg://user:password@postgres/property_agent_demo", "demo", False),
    ],
)
def test_demo_reset_rejects_unsafe_target(url: str, environment: str, confirmed: bool) -> None:
    with pytest.raises(RuntimeError, match="Reset refused"):
        validate_target(url, environment, confirmed)
