from dataclasses import dataclass
from uuid import UUID

from property_agent.platform.roles import Role


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Trusted identity and tenancy data injected by the authentication layer."""

    actor_id: UUID
    community_id: UUID
    roles: frozenset[Role]
    request_id: str
    house_ids: frozenset[UUID] = frozenset()

    def __post_init__(self) -> None:
        if not self.request_id.strip() or len(self.request_id) > 64:
            raise ValueError("request_id must contain 1 to 64 non-whitespace characters.")

    def has_any_role(self, *roles: Role) -> bool:
        return bool(self.roles.intersection(roles))
