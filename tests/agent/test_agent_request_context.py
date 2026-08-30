from uuid import uuid4

from property_agent.agent.adapters.api.dependencies import AgentRequestContext
from property_agent.platform.context import RequestContext
from property_agent.repair.domain.enums import Role


def test_agent_context_preserves_business_service_role_protocol() -> None:
    house_id = uuid4()
    platform = RequestContext(
        actor_id=uuid4(),
        community_id=uuid4(),
        roles=frozenset({"RESIDENT"}),
        request_id="req_agent_context",
        current_house_id=house_id,
        bound_house_ids=frozenset({house_id}),
    )

    context = AgentRequestContext.from_platform(platform)

    assert context.has_any_role(Role.RESIDENT)
    assert not context.has_any_role(Role.MANAGER)
    assert context.house_ids == context.bound_house_ids == frozenset({house_id})
