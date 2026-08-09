"""
Platform infrastructure ORM models — shared SQLAlchemy Base and 10 core tables.

PRD 5.1: Community, User, House, UserRole, UserHouseBinding,
ConfirmationToken, IdempotencyRecord, MessageRecord, AuditLog, HandoverTicket.

This module provides the *single shared Base* that all domain modules
(repair, inspection, billing, announcement, agent) should inherit from.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ═══════════════════════════════════════════════════════════════
# Shared Base — the single SQLAlchemy DeclarativeBase for ALL modules
# ═══════════════════════════════════════════════════════════════

class Base(DeclarativeBase):
    """Unified SQLAlchemy base for the entire property-community-agent project.

    All domain modules MUST import and use this Base instead of defining
    their own DeclarativeBase subclasses.
    """
    pass


# ═══════════════════════════════════════════════════════════════
# 1. Community — business data isolation root
# ═══════════════════════════════════════════════════════════════

class CommunityModel(Base):
    __tablename__ = "communities"
    __table_args__ = (
        UniqueConstraint("name", name="uq_communities_name"),
        Index("ix_communities_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="社区唯一标识"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="社区名称")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE",
        comment="状态: ACTIVE / INACTIVE / MAINTENANCE"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now(),
        comment="更新时间"
    )

    houses: Mapped[list["HouseModel"]] = relationship(
        back_populates="community", cascade="all, delete-orphan"
    )
    users: Mapped[list["UserModel"]] = relationship(
        back_populates="community", cascade="all, delete-orphan"
    )


# ═══════════════════════════════════════════════════════════════
# 2. User — demo accounts and staff accounts
# ═══════════════════════════════════════════════════════════════

class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("community_id", "username", name="uq_users_community_username"),
        Index("ix_users_community_status", "community_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="用户唯一标识"
    )
    community_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("communities.id", ondelete="RESTRICT"),
        nullable=False, comment="所属社区"
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, comment="登录用户名")
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示名称")
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False, comment="密码哈希(演示用)")
    phone: Mapped[str | None] = mapped_column(String(20), comment="手机号(脱敏存储)")
    email: Mapped[str | None] = mapped_column(String(128), comment="邮箱")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE",
        comment="状态: ACTIVE / INACTIVE / FROZEN"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now(),
        comment="更新时间"
    )

    community: Mapped[CommunityModel] = relationship(back_populates="users")
    roles: Mapped[list["UserRoleModel"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    house_bindings: Mapped[list["UserHouseBindingModel"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


# ═══════════════════════════════════════════════════════════════
# 3. House — basic house data
# ═══════════════════════════════════════════════════════════════

class HouseModel(Base):
    __tablename__ = "houses"
    __table_args__ = (
        UniqueConstraint("community_id", "building", "unit", "room_no",
                         name="uq_houses_address"),
        Index("ix_houses_community_status", "community_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="房屋唯一标识"
    )
    community_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("communities.id", ondelete="RESTRICT"),
        nullable=False, comment="所属社区"
    )
    building: Mapped[str] = mapped_column(String(32), nullable=False, comment="楼栋")
    unit: Mapped[str] = mapped_column(String(16), nullable=False, comment="单元")
    room_no: Mapped[str] = mapped_column(String(16), nullable=False, comment="房号")
    house_type: Mapped[str | None] = mapped_column(
        String(32), comment="房屋类型: RESIDENTIAL / SHOP / OFFICE / PARKING (公告受众维度)"
    )
    area: Mapped[float | None] = mapped_column(comment="建筑面积(m²)")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE",
        comment="状态: ACTIVE / INACTIVE / DECORATING"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now(),
        comment="更新时间"
    )

    community: Mapped[CommunityModel] = relationship(back_populates="houses")
    bindings: Mapped[list["UserHouseBindingModel"]] = relationship(
        back_populates="house", cascade="all, delete-orphan"
    )


# ═══════════════════════════════════════════════════════════════
# 4. UserRole — user role and authorization scope
# ═══════════════════════════════════════════════════════════════

class UserRoleModel(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role", "scope", name="uq_user_roles"),
        Index("ix_user_roles_user", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="角色记录ID"
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="用户ID"
    )
    role: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="角色: RESIDENT / CUSTOMER_SERVICE / REPAIR_WORKER / "
                "SECURITY_GUARD / FINANCE / MANAGER / SYSTEM_ADMIN"
    )
    scope: Mapped[str] = mapped_column(
        String(64), nullable=False, default="*",
        comment="授权范围: * 表示全社区, 或指定楼栋/区域"
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="生效时间"
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="失效时间, NULL 表示永久有效"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )

    user: Mapped[UserModel] = relationship(back_populates="roles")


# ═══════════════════════════════════════════════════════════════
# 5. UserHouseBinding — resident-house binding
# ═══════════════════════════════════════════════════════════════

class UserHouseBindingModel(Base):
    __tablename__ = "user_house_bindings"
    __table_args__ = (
        UniqueConstraint("user_id", "house_id", name="uq_user_house_bindings"),
        Index("ix_user_house_bindings_user", "user_id"),
        Index("ix_user_house_bindings_house", "house_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="绑定记录ID"
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="用户ID"
    )
    house_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("houses.id", ondelete="CASCADE"),
        nullable=False, comment="房屋ID"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE",
        comment="状态: ACTIVE / INACTIVE / EXPIRED"
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="生效时间"
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="失效时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )

    user: Mapped[UserModel] = relationship(back_populates="house_bindings")
    house: Mapped[HouseModel] = relationship(back_populates="bindings")


# ═══════════════════════════════════════════════════════════════
# 6. ConfirmationToken — write operation secondary confirmation
# ═══════════════════════════════════════════════════════════════

class ConfirmationTokenModel(Base):
    __tablename__ = "confirmation_tokens"
    __table_args__ = (
        Index("ix_confirmation_tokens_actor", "actor_id"),
        Index("ix_confirmation_tokens_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="令牌ID"
    )
    token: Mapped[str] = mapped_column(
        String(256), nullable=False, unique=True, comment="令牌值"
    )
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="操作人ID"
    )
    action: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="操作类型"
    )
    parameter_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="参数哈希"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="过期时间"
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="消费时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )


# ═══════════════════════════════════════════════════════════════
# 7. IdempotencyRecord — prevent duplicate writes
# ═══════════════════════════════════════════════════════════════

class IdempotencyRecordModel(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "key",
                         name="uq_idempotency_actor_op_key"),
        Index("ix_idempotency_expires", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="记录ID"
    )
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="操作人ID"
    )
    operation: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="操作类型"
    )
    key: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="幂等键"
    )
    request_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="请求体哈希"
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(64), comment="创建的资源ID"
    )
    response_snapshot: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), comment="首次响应快照"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )


# ═══════════════════════════════════════════════════════════════
# 8. MessageRecord — outbox for station messages
# ═══════════════════════════════════════════════════════════════

class MessageRecordModel(Base):
    __tablename__ = "message_records"
    __table_args__ = (
        Index("ix_message_records_receiver_status", "receiver_id", "status"),
        Index("ix_message_records_business", "business_type", "resource_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="消息ID"
    )
    receiver_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="接收人ID"
    )
    business_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="业务类型: REPAIR / ANNOUNCEMENT / BILLING / INSPECTION"
    )
    resource_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="关联资源ID"
    )
    title: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="消息标题"
    )
    body: Mapped[str] = mapped_column(
        Text, nullable=False, comment="消息正文"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING",
        comment="状态: PENDING / SENT / FAILED / READ"
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="重试次数"
    )
    last_error: Mapped[str | None] = mapped_column(
        Text, comment="最后一次错误信息"
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="消息去重键"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now(),
        comment="更新时间"
    )


# ═══════════════════════════════════════════════════════════════
# 9. AuditLog — critical operation evidence
# ═══════════════════════════════════════════════════════════════

class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_actor_created", "actor_id", "created_at"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_action", "action"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="审计记录ID"
    )
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="操作人ID"
    )
    community_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="社区ID"
    )
    action: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="操作: LOGIN_SUCCESS / LOGIN_FAILED / "
        "ACCESS_DENIED / BILL_QUERY / REPAIR_CREATE / ANNOUNCEMENT_PUBLISH / etc."
    )
    resource_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="资源类型: WORK_ORDER / BILL / ANNOUNCEMENT / EVENT"
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(64), comment="资源ID"
    )
    parameter_summary: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), comment="参数摘要(脱敏)"
    )
    result: Mapped[str] = mapped_column(
        String(16), nullable=False, default="SUCCESS",
        comment="结果: SUCCESS / FAILURE / DENIED"
    )
    request_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="请求ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )


# ═══════════════════════════════════════════════════════════════
# 10. HandoverTicket — manual takeover
# ═══════════════════════════════════════════════════════════════

class HandoverTicketModel(Base):
    __tablename__ = "handover_tickets"
    __table_args__ = (
        Index("ix_handover_tickets_status", "status"),
        Index("ix_handover_tickets_queue", "queue"),
        Index("ix_handover_tickets_community", "community_id"),
        Index("ix_handover_tickets_resource", "resource_type", "resource_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="接管单ID"
    )
    community_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), comment="所属社区(数据隔离)"
    )
    requester_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), comment="发起人ID"
    )
    resource_type: Mapped[str | None] = mapped_column(
        String(32), comment="关联资源类型: WORK_ORDER / ANNOUNCEMENT / BILL / EVENT"
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(64), comment="关联资源ID"
    )
    request_id: Mapped[str | None] = mapped_column(
        String(64), comment="发起请求ID(链路追踪)"
    )
    payload: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), comment="接管上下文(脱敏)"
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="来源: REPAIR / ANNOUNCEMENT / BILLING / INSPECTION / AGENT"
    )
    queue: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="队列: CUSTOMER_SERVICE / REPAIR / SECURITY / MANAGEMENT"
    )
    summary: Mapped[str] = mapped_column(
        Text, nullable=False, comment="接管摘要"
    )
    reason: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="接管原因: HIGH_RISK / AI_FAILURE / MANUAL_REQUEST"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING",
        comment="状态: PENDING / ASSIGNED / PROCESSING / RESOLVED / CLOSED"
    )
    assignee_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), comment="指派人ID"
    )
    resolution: Mapped[str | None] = mapped_column(
        Text, comment="处理结果"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now(),
        comment="更新时间"
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="关闭时间"
    )


# ═══════════════════════════════════════════════════════════════
# 11. Attachment — uploaded file registry (PRD 6.1 附件校验)
# ═══════════════════════════════════════════════════════════════

ATTACHMENT_MAX_SIZE_BYTES = 10 * 1024 * 1024
ATTACHMENT_ALLOWED_CONTENT_TYPES = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "video/mp4",
    "application/pdf",
})


class AttachmentModel(Base):
    """Uploaded file metadata.

    Business modules never trust a client-supplied attachment ID directly:
    ``AttachmentPort.ensure_usable`` verifies community scope, uploader
    ownership, upload status, declared content type and size before an
    attachment can be linked to a work order / event / announcement.
    """

    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_community_status", "community_id", "status"),
        Index("ix_attachments_uploader", "uploader_id"),
        CheckConstraint("size_bytes >= 0", name="ck_attachments_size_non_negative"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="附件ID"
    )
    community_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="所属社区"
    )
    uploader_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, comment="上传人ID"
    )
    file_name: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="原始文件名"
    )
    content_type: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="MIME 类型"
    )
    size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="文件大小(字节)"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="UPLOADING",
        comment="状态: UPLOADING / UPLOADED / FAILED / DELETED"
    )
    storage_key: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="对象存储键"
    )
    business_type: Mapped[str | None] = mapped_column(
        String(32), comment="业务类型: REPAIR / INSPECTION / ANNOUNCEMENT"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now(),
        comment="更新时间"
    )