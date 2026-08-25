"""智能体对外接口 — PRD §6.5.2（FastAPI / SSE 入口）。

    POST   /api/agent/conversations/{conversation_id}/messages         发起一轮
    POST   /api/agent/conversations/{conversation_id}/messages/stream  同上，SSE
    POST   /api/agent/conversations/{conversation_id}/confirmations    确认/取消
    GET    /api/agent/conversations/{conversation_id}                  会话状态
    DELETE /api/agent/conversations/{conversation_id}                  结束会话

约束：

* ``conversation_id`` 由调用方给出且稳定，直接用作 Checkpointer 的 thread_id；
* 身份只从可信上下文取，``house_id`` 必须在绑定列表内，否则 403；
* 确认接口是恢复的唯一入口，必然先过恢复守卫的四道闸。
"""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse

from property_agent.agent.adapters.api.dependencies import (
    AgentRequestContext,
    get_agent_context,
    get_agent_runner,
)
from property_agent.agent.adapters.api.presentation import (
    status_data,
    turn_data,
    wire_events,
)
from property_agent.agent.adapters.api.schemas import ConfirmRequest, SendMessageRequest
from property_agent.agent.adapters.api.stream_delivery import BoundedStreamBridge
from property_agent.agent.application.facade import AgentRuntimeFacade
from property_agent.platform.errors import BusinessError
from property_agent.platform.schemas import Envelope

router = APIRouter(prefix="/api/agent/conversations", tags=["agent"])

RunnerDep = Annotated[AgentRuntimeFacade, Depends(get_agent_runner)]
ContextDep = Annotated[AgentRequestContext, Depends(get_agent_context)]
ConversationId = Annotated[str, Path(min_length=1, max_length=64)]


def _envelope(data: object, context: AgentRequestContext) -> Envelope:
    return Envelope(success=True, data=data, error=None, request_id=context.request_id)


def _resolve_house(context: AgentRequestContext, house_id: UUID | None) -> UUID | None:
    """房屋只认绑定列表；请求体里的房屋 ID 必须能在可信上下文中找到。"""
    if house_id is None:
        return context.current_house_id
    if house_id not in context.house_ids:
        raise BusinessError(
            "HOUSE_NOT_BOUND", "The requested house is not bound to this account.", 403
        )
    return house_id


@router.post("/{conversation_id}/messages", response_model=Envelope)
def send_message(
    conversation_id: ConversationId,
    payload: SendMessageRequest,
    runner: RunnerDep,
    context: ContextDep,
) -> Envelope:
    turn = runner.start(
        conversation_id=conversation_id,
        context=context,
        user_text=payload.text,
        house_id=_resolve_house(context, payload.house_id),
        slots=dict(payload.slots or {}),
    )
    return _envelope(turn_data(turn), context)


@router.post("/{conversation_id}/messages/stream")
def send_message_stream(
    conversation_id: ConversationId,
    payload: SendMessageRequest,
    runner: RunnerDep,
    context: ContextDep,
    request: Request,
) -> StreamingResponse:
    """真流式消息接口（P1 观测与流式）。

    Runner yields a bounded internal event contract. This adapter projects it onto the
    compatible run/progress/intent/message/confirmation/facts/turn/done SSE family without
    exposing graph node names or checkpointed state.
    """

    house_id = _resolve_house(context, payload.house_id)
    bridge = BoundedStreamBridge(
        lambda: runner.stream_start(
            conversation_id=conversation_id,
            context=context,
            user_text=payload.text,
            house_id=house_id,
            slots=dict(payload.slots or {}),
        ),
        registry=request.app.state.agent_stream_executions,
        observability=getattr(request.app.state, "agent_observability", None),
    )

    def _stream():
        for event in bridge.events():
            for name, data in wire_events(event):
                body = json.dumps(data, ensure_ascii=False, default=str)
                if len(body.encode("utf-8")) > 262_144:
                    raise RuntimeError("SSE event exceeds the bounded presentation limit")
                yield f"event: {name}\ndata: {body}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{conversation_id}/confirmations", response_model=Envelope)
def confirm(
    conversation_id: ConversationId,
    payload: ConfirmRequest,
    runner: RunnerDep,
    context: ContextDep,
) -> Envelope:
    turn = runner.resume(
        conversation_id=conversation_id,
        context=context,
        confirmed=payload.confirmed,
        action_hash=payload.action_hash,
    )
    return _envelope(turn_data(turn), context)


@router.get("/{conversation_id}", response_model=Envelope)
def get_conversation(
    conversation_id: ConversationId,
    runner: RunnerDep,
    context: ContextDep,
) -> Envelope:
    conversation, pending = runner.status(conversation_id=conversation_id, context=context)
    return _envelope(status_data(conversation, pending), context)


@router.delete("/{conversation_id}", response_model=Envelope)
def close_conversation(
    conversation_id: ConversationId,
    runner: RunnerDep,
    context: ContextDep,
) -> Envelope:
    conversation = runner.close(conversation_id=conversation_id, context=context)
    return _envelope(status_data(conversation, None), context)
