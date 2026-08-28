"""Server-side confirmation-token and approval preparation."""

from typing import Any

from property_agent.agent.application.confirm_params import derive_confirmation_params
from property_agent.agent.state import GraphState
from property_agent.platform.application.approval_service import ApprovalService
from property_agent.platform.application.confirmation_service import ConfirmationService


def prepare_confirmation(
    state: GraphState,
    *,
    session_factory: Any,
    approval_service: ApprovalService,
    announcement_service: Any,
) -> str:
    """Issue a token and promote the matching approval to APPROVED.

    The runner calls this inside lease ownership after the user confirms. The
    business Unit of Work still consumes the approval and token atomically with
    its mutation.
    """
    action, parameters = derive_confirmation_params(
        state, announcement_service=announcement_service
    )
    with session_factory() as session:
        token = ConfirmationService(session).generate_token(
            actor_id=state.actor_id,
            action=action,
            params=parameters,
        )
        session.commit()

    conversation_id = str(state.conversation_id or "")
    if conversation_id:
        approval = approval_service.create_pending(
            conversation_id=conversation_id,
            actor_id=state.actor_id,
            action=action,
            params=parameters,
        )
        approval_service.approve(approval_id=approval.id, actor_id=state.actor_id)
        state.approval_ref = str(approval.id)
    return token
