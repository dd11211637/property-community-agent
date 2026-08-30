"""Authenticated local attachment storage for the real product runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from property_agent.platform.adapters.api.dependencies import RequestContext
from property_agent.platform.errors import BusinessError
from property_agent.platform.infrastructure.orm_models import (
    ATTACHMENT_ALLOWED_CONTENT_TYPES,
    ATTACHMENT_MAX_SIZE_BYTES,
    AttachmentModel,
)
from property_agent.repair.infrastructure.models import (
    WorkOrderModel,
    WorkOrderProcessRecordModel,
)


@dataclass(frozen=True, slots=True)
class AttachmentFile:
    record: AttachmentModel
    path: Path


class LocalAttachmentStorage:
    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()

    def write(self, community_id: UUID, attachment_id: UUID, content: bytes) -> str:
        folder = self._root / str(community_id)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / str(attachment_id)
        temporary = folder / f".{attachment_id}.uploading"
        temporary.write_bytes(content)
        temporary.replace(target)
        return f"{community_id}/{attachment_id}"

    def resolve(self, storage_key: str) -> Path:
        target = (self._root / storage_key).resolve()
        if self._root not in target.parents:
            raise BusinessError("ATTACHMENT_PATH_INVALID", "附件存储路径无效。", 500)
        return target

    def remove(self, storage_key: str) -> None:
        target = self.resolve(storage_key)
        if target.exists():
            target.unlink()


class AttachmentService:
    def __init__(self, session: Session, storage: LocalAttachmentStorage) -> None:
        self._session = session
        self._storage = storage

    def upload(
        self,
        context: RequestContext,
        *,
        file_name: str,
        content_type: str,
        content: bytes,
        business_type: str,
    ) -> AttachmentModel:
        self._validate(file_name, content_type, content, business_type)
        attachment_id = uuid4()
        storage_key = self._storage.write(context.community_id, attachment_id, content)
        now = datetime.now(UTC)
        record = AttachmentModel(
            id=attachment_id,
            community_id=context.community_id,
            uploader_id=context.actor_id,
            file_name=Path(file_name).name,
            content_type=content_type,
            size_bytes=len(content),
            status="UPLOADED",
            storage_key=storage_key,
            business_type=business_type,
            created_at=now,
            updated_at=now,
        )
        try:
            self._session.add(record)
            self._session.commit()
        except Exception:
            self._session.rollback()
            self._storage.remove(storage_key)
            raise
        return record

    def open(self, context: RequestContext, attachment_id: UUID) -> AttachmentFile:
        record = self._session.scalar(
            select(AttachmentModel).where(
                AttachmentModel.id == attachment_id,
                AttachmentModel.community_id == context.community_id,
                AttachmentModel.status == "UPLOADED",
            )
        )
        if record is None or not self._can_access(context, record):
            raise BusinessError("ATTACHMENT_NOT_FOUND", "附件不存在或不可访问。", 404)
        path = self._storage.resolve(record.storage_key)
        if not path.is_file():
            raise BusinessError("ATTACHMENT_FILE_MISSING", "附件文件暂不可用。", 503)
        return AttachmentFile(record, path)

    def _can_access(self, context: RequestContext, record: AttachmentModel) -> bool:
        if record.uploader_id == context.actor_id:
            return True
        if context.has_any_role("CUSTOMER_SERVICE", "MANAGER", "ADMIN"):
            return True
        if record.business_type != "REPAIR":
            return False
        return any(
            self._visible_repair(context, order, record.id)
            for order in self._session.scalars(
                select(WorkOrderModel).where(WorkOrderModel.community_id == context.community_id)
            )
        )

    def _visible_repair(
        self, context: RequestContext, order: WorkOrderModel, attachment_id: UUID
    ) -> bool:
        value = str(attachment_id)
        linked = value in (order.request_attachment_ids or ())
        if not linked:
            linked = any(
                value in (record.attachment_ids or ())
                for record in self._session.scalars(
                    select(WorkOrderProcessRecordModel).where(
                        WorkOrderProcessRecordModel.work_order_id == order.id
                    )
                )
            )
        if not linked:
            return False
        if context.has_any_role("REPAIR_WORKER"):
            return order.assignee_id == context.actor_id
        return order.house_id in context.house_ids

    @staticmethod
    def _validate(file_name: str, content_type: str, content: bytes, business_type: str) -> None:
        if not Path(file_name).name or len(Path(file_name).name) > 256:
            raise BusinessError("ATTACHMENT_NAME_INVALID", "附件名称无效。", 422)
        if content_type not in ATTACHMENT_ALLOWED_CONTENT_TYPES:
            raise BusinessError("ATTACHMENT_TYPE_INVALID", "不支持该附件类型。", 422)
        if not content or len(content) > ATTACHMENT_MAX_SIZE_BYTES:
            raise BusinessError("ATTACHMENT_SIZE_INVALID", "附件大小必须在 10MB 以内。", 422)
        if business_type not in {"REPAIR", "INSPECTION", "ANNOUNCEMENT"}:
            raise BusinessError("ATTACHMENT_BUSINESS_INVALID", "附件业务类型无效。", 422)
