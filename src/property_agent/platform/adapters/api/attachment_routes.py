"""Authenticated attachment upload and download endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from property_agent.config import settings
from property_agent.platform.adapters.api.dependencies import RequestContext, get_current_user
from property_agent.platform.application.attachment_service import (
    AttachmentService,
    LocalAttachmentStorage,
)
from property_agent.platform.infrastructure.database import get_db
from property_agent.platform.infrastructure.orm_models import ATTACHMENT_MAX_SIZE_BYTES
from property_agent.platform.schemas import Envelope

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


class AttachmentResponse(BaseModel):
    id: UUID
    file_name: str
    content_type: str
    size_bytes: int
    status: str
    business_type: str | None


AttachmentEnvelope = Envelope[AttachmentResponse]
ContextDependency = Annotated[RequestContext, Depends(get_current_user)]
DatabaseDependency = Annotated[Session, Depends(get_db)]


@router.post("", response_model=AttachmentEnvelope, status_code=201)
async def upload_attachment(
    context: ContextDependency,
    db: DatabaseDependency,
    file: UploadFile = File(...),  # noqa: B008
    business_type: str = Form("REPAIR"),  # noqa: B008
) -> Envelope:
    content = await file.read(ATTACHMENT_MAX_SIZE_BYTES + 1)
    await file.close()
    service = AttachmentService(db, LocalAttachmentStorage(settings.attachment_storage_root))
    record = service.upload(
        context,
        file_name=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
        content=content,
        business_type=business_type.upper(),
    )
    data = AttachmentResponse(
        id=record.id,
        file_name=record.file_name,
        content_type=record.content_type,
        size_bytes=record.size_bytes,
        status=record.status,
        business_type=record.business_type,
    )
    return Envelope(success=True, data=data, error=None, request_id=context.request_id)


@router.get("/{attachment_id}", response_class=FileResponse)
def download_attachment(
    attachment_id: UUID,
    context: ContextDependency,
    db: DatabaseDependency,
) -> FileResponse:
    service = AttachmentService(db, LocalAttachmentStorage(settings.attachment_storage_root))
    attachment = service.open(context, attachment_id)
    return FileResponse(
        attachment.path,
        media_type=attachment.record.content_type,
        filename=attachment.record.file_name,
    )
