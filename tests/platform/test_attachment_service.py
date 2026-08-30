from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from property_agent.platform.adapters.api.dependencies import RequestContext
from property_agent.platform.application.attachment_service import (
    AttachmentService,
    LocalAttachmentStorage,
)
from property_agent.platform.errors import BusinessError
from property_agent.platform.infrastructure.orm_models import Base


def context(*, community_id=None, actor_id=None, roles=()) -> RequestContext:
    return RequestContext(
        actor_id=actor_id or uuid4(),
        community_id=community_id or uuid4(),
        roles=frozenset(roles),
        request_id="req_attachment_test",
    )


def test_upload_persists_bytes_and_owner_can_download(tmp_path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    owner = context()
    with Session(engine) as session:
        service = AttachmentService(session, LocalAttachmentStorage(str(tmp_path)))
        record = service.upload(
            owner,
            file_name="漏水现场.png",
            content_type="image/png",
            content=b"real-image-bytes",
            business_type="REPAIR",
        )
        attachment = service.open(owner, record.id)
        assert attachment.path.read_bytes() == b"real-image-bytes"
        assert attachment.record.file_name == "漏水现场.png"


def test_attachment_is_hidden_from_another_community(tmp_path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    owner = context()
    with Session(engine) as session:
        service = AttachmentService(session, LocalAttachmentStorage(str(tmp_path)))
        record = service.upload(
            owner,
            file_name="evidence.pdf",
            content_type="application/pdf",
            content=b"pdf",
            business_type="REPAIR",
        )
        with pytest.raises(BusinessError) as caught:
            service.open(context(), record.id)
        assert caught.value.code == "ATTACHMENT_NOT_FOUND"


def test_upload_rejects_unapproved_content_type(tmp_path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = AttachmentService(session, LocalAttachmentStorage(str(tmp_path)))
        with pytest.raises(BusinessError) as caught:
            service.upload(
                context(),
                file_name="payload.exe",
                content_type="application/octet-stream",
                content=b"unsafe",
                business_type="REPAIR",
            )
        assert caught.value.code == "ATTACHMENT_TYPE_INVALID"
