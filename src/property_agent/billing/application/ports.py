"""
application/ports.py     端口接口（Port）

定义领域层对外部依赖的抽象接口。
具体实现放在 infrastructure 层。
每个接口方法标注了等价 SQL 语句。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from ..domain.entities import (
    Bill,
    BillingRule,
    Building,
    ConsultationTicket,
    Payment,
    Receipt,
    Room,
    User,
)

# ── 仓储端口 ─────────────────────────────────────────


class BillRepository(ABC):
    """
    账单仓储接口

    具体实现: infrastructure/repositories.py → SqlAlchemyBillRepository
    SQL 等价:
        SELECT * FROM fee_bills WHERE user_id = :user_id;
        SELECT * FROM fee_bills WHERE bill_id = :bill_id;
        UPDATE fee_bills SET status = 'PAID' WHERE bill_id = :bill_id;
    """

    @abstractmethod
    def find_by_user(self, user_id: str) -> list[Bill]:
        """
        查询用户的所有账单

        SQL:
            SELECT f.*, u.user_name, r.room_number, b.building_name
              FROM fee_bills f
              JOIN sys_users u ON f.user_id = u.user_id
              JOIN community_rooms r ON f.room_id = r.room_id
              JOIN community_buildings b ON r.building_id = b.building_id
             WHERE f.user_id = :user_id
             ORDER BY f.bill_period DESC;
        """
        ...

    @abstractmethod
    def find_by_id(self, bill_id: str) -> Bill | None:
        """
        按 ID 查询账单

        SQL:
            SELECT * FROM fee_bills WHERE bill_id = :bill_id;
        """
        ...

    @abstractmethod
    def find_by_building(self, building_id: str) -> list[Bill]:
        """
        按楼栋查询账单（物业员工用）

        SQL:
            SELECT f.* FROM fee_bills f
            JOIN community_rooms r ON f.room_id = r.room_id
            WHERE r.building_id = :building_id
            ORDER BY f.bill_period DESC;
        """
        ...

    @abstractmethod
    def find_all(self) -> list[Bill]:
        """
        查询所有账单（管理员用）

        SQL:
            SELECT * FROM fee_bills ORDER BY bill_period DESC;
        """
        ...

    @abstractmethod
    def save(self, bill: Bill) -> Bill:
        """
        保存账单（更新）

        SQL:
            UPDATE fee_bills
               SET status = :status,
                   payment_time = :payment_time,
                   receipt_no = :receipt_no,
                   late_fee = :late_fee,
                   total_amount = :total_amount,
                   updated_at = NOW()
             WHERE bill_id = :bill_id;
        """
        ...

    @abstractmethod
    def find_unpaid_or_overdue(self, user_id: str) -> list[Bill]:
        """
        查询未缴费/逾期账单

        SQL:
            SELECT * FROM fee_bills
            WHERE user_id = :user_id AND status IN ('UNPAID', 'OVERDUE');
        """
        ...

    @abstractmethod
    def find_unpaid_before_date(self, check_date: date) -> list[Bill]:
        """
        查询所有 UNPAID 且到期日早于指定日期的账单（逾期检查用）

        SQL:
            SELECT * FROM fee_bills
            WHERE status = 'UNPAID'
              AND due_date < :check_date;
        """
        ...

    @abstractmethod
    def exists_by_user_and_period(self, user_id: str, period: str) -> bool:
        """
        检查指定用户+账期的账单是否已存在

        SQL:
            SELECT COUNT(*) FROM fee_bills
            WHERE user_id = :user_id AND bill_period = :period;
        """
        ...

    @abstractmethod
    def create(self, bill: Bill) -> Bill:
        """
        创建新账单

        SQL:
            INSERT INTO fee_bills
                (bill_id, user_id, room_id, bill_period, property_fee, utility_fee,
                 parking_fee, late_fee, total_amount, due_date, status, created_at, updated_at)
            VALUES
                (:bill_id, :user_id, :room_id, :period, :property_fee, :utility_fee,
                 :parking_fee, 0, :total, :due_date, 'UNPAID', NOW(), NOW());
        """
        ...

    @abstractmethod
    def bulk_update_status(self, bill_ids: list[str], new_status: str) -> int:
        """
        批量更新账单状态

        SQL:
            UPDATE fee_bills
               SET status = :new_status,
                   updated_at = NOW()
             WHERE bill_id IN (:bill_ids);
        """
        ...


class UserRepository(ABC):
    """
    用户仓储接口

    具体实现: infrastructure/repositories.py → SqlAlchemyUserRepository
    SQL 等价:
        SELECT * FROM sys_users WHERE user_id = :user_id;
    """

    @abstractmethod
    def find_by_id(self, user_id: str) -> User | None:
        """
        按 ID 查询用户

        SQL:
            SELECT u.*, b.building_name, r.room_number
              FROM sys_users u
              LEFT JOIN community_buildings b ON u.building_id = b.building_id
              LEFT JOIN community_rooms r ON u.room_id = r.room_id
             WHERE u.user_id = :user_id;
        """
        ...

    @abstractmethod
    def find_owner_by_room(self, room_id: str) -> User | None:
        """
        按房号查询活跃业主（账单生成用）

        SQL:
            SELECT * FROM sys_users
             WHERE room_id = :room_id
               AND role = 'owner'
               AND status = 'ACTIVE'
             LIMIT 1;
        """
        ...


class PaymentRepository(ABC):
    """
    缴费记录仓储接口

    SQL 等价:
        INSERT INTO fee_payments (...) VALUES (...);
        SELECT * FROM fee_payments WHERE user_id = :user_id ORDER BY paid_at DESC;
        SELECT payment_id FROM fee_payments ORDER BY payment_id DESC LIMIT 1;
    """

    @abstractmethod
    def get_last_payment_id(self) -> str | None:
        """
        获取最新支付记录ID（用于生成新ID）

        SQL:
            SELECT payment_id FROM fee_payments ORDER BY payment_id DESC LIMIT 1;
        """
        ...

    @abstractmethod
    def save(self, payment: Payment) -> Payment:
        """
        保存缴费记录

        SQL:
            INSERT INTO fee_payments
                (payment_id, bill_id, user_id, pay_amount, pay_method, pay_status,
                 transaction_id, receipt_no, paid_at, created_at)
            VALUES
                (:payment_id, :bill_id, :user_id, :amount, :method, :status,
                 :txn_id, :receipt_no, :paid_at, NOW());
        """
        ...

    @abstractmethod
    def find_by_user(self, user_id: str) -> list[Payment]:
        """
        查询用户的缴费记录

        SQL:
            SELECT p.*, u.user_name
              FROM fee_payments p
              JOIN sys_users u ON p.user_id = u.user_id
             WHERE p.user_id = :user_id
             ORDER BY p.paid_at DESC;
        """
        ...

    @abstractmethod
    def find_all(self) -> list[Payment]:
        """
        查询所有缴费记录（管理员用）

        SQL:
            SELECT * FROM fee_payments ORDER BY paid_at DESC;
        """
        ...

    @abstractmethod
    def find_by_bill_id(self, bill_id: str) -> Payment | None:
        """
        按账单ID查询支付记录（退款用）

        SQL:
            SELECT p.*, u.user_name
              FROM fee_payments p
              JOIN sys_users u ON p.user_id = u.user_id
             WHERE p.bill_id = :bill_id
               AND p.pay_status = 'SUCCESS'
             LIMIT 1;
        """
        ...

    @abstractmethod
    def update_status(self, payment_id: str, new_status: str) -> None:
        """
        更新支付记录状态

        SQL:
            UPDATE fee_payments
               SET pay_status = :new_status,
                   updated_at = NOW()
             WHERE payment_id = :payment_id;
        """
        ...


class ReceiptRepository(ABC):
    """
    票据仓储接口

    SQL 等价:
        SELECT * FROM fee_receipts WHERE receipt_no = :receipt_no;
        INSERT INTO fee_receipts (...) VALUES (...);
    """

    @abstractmethod
    def find_by_no(self, receipt_no: str) -> Receipt | None:
        """
        按票据号查询

        SQL:
            SELECT r.*, u.user_name, b.building_name, rm.room_number, p.paid_at
              FROM fee_receipts r
              JOIN sys_users u ON r.user_id = u.user_id
              LEFT JOIN community_buildings b ON u.building_id = b.building_id
              LEFT JOIN community_rooms rm ON u.room_id = rm.room_id
              JOIN fee_payments p ON r.payment_id = p.payment_id
             WHERE r.receipt_no = :receipt_no;
        """
        ...

    @abstractmethod
    def save(self, receipt: Receipt) -> Receipt:
        """
        保存票据

        SQL:
            INSERT INTO fee_receipts
                (receipt_no, bill_id, user_id, payment_id, period,
                 property_fee, utility_fee, parking_fee, late_fee, total_amount,
                 issue_time, is_valid, created_at)
            VALUES
                (:receipt_no, :bill_id, :user_id, :payment_id, :period,
                 :property_fee, :utility_fee, :parking_fee, :late_fee, :total,
                 :issue_time, TRUE, NOW());
        """
        ...

    @abstractmethod
    def invalidate_by_bill_id(self, bill_id: str) -> None:
        """
        按账单ID作废票据（退款用）

        SQL:
            UPDATE fee_receipts
               SET is_valid = FALSE
             WHERE bill_id = :bill_id;
        """
        ...


# ── 楼栋/房号仓储端口 ───────────────────────────────


class BuildingRepository(ABC):
    """
    楼栋仓储接口

    SQL 等价:
        SELECT * FROM community_buildings WHERE building_id = :building_id;
        SELECT * FROM community_buildings ORDER BY building_name;
    """

    @abstractmethod
    def find_by_id(self, building_id: str) -> Building | None:
        """
        按 ID 查询楼栋

        SQL:
            SELECT * FROM community_buildings WHERE building_id = :building_id;
        """
        ...

    @abstractmethod
    def find_all(self) -> list[Building]:
        """
        查询所有楼栋

        SQL:
            SELECT * FROM community_buildings ORDER BY building_name;
        """
        ...


class RoomRepository(ABC):
    """
    房号仓储接口

    SQL 等价:
        SELECT * FROM community_rooms WHERE room_id = :room_id;
        SELECT * FROM community_rooms WHERE building_id = :building_id;
    """

    @abstractmethod
    def find_by_id(self, room_id: str) -> Room | None:
        """
        按 ID 查询房号

        SQL:
            SELECT r.*, b.building_name
              FROM community_rooms r
              JOIN community_buildings b ON r.building_id = b.building_id
             WHERE r.room_id = :room_id;
        """
        ...

    @abstractmethod
    def find_by_building(self, building_id: str) -> list[Room]:
        """
        按楼栋查询房号列表

        SQL:
            SELECT r.*, b.building_name
              FROM community_rooms r
              JOIN community_buildings b ON r.building_id = b.building_id
             WHERE r.building_id = :building_id
             ORDER BY r.room_number;
        """
        ...

    @abstractmethod
    def find_all(self) -> list[Room]:
        """
        查询所有房号

        SQL:
            SELECT r.*, b.building_name
              FROM community_rooms r
              JOIN community_buildings b ON r.building_id = b.building_id
             ORDER BY b.building_name, r.room_number;
        """
        ...

    @abstractmethod
    def find_active_rooms(self) -> list[Room]:
        """
        查询所有活跃房号（OCCUPIED 状态，账单生成用）

        SQL:
            SELECT r.*, b.building_name
              FROM community_rooms r
              JOIN community_buildings b ON r.building_id = b.building_id
             WHERE r.status = 'OCCUPIED';
        """
        ...


# ── 事务管理端口 ─────────────────────────────────────


class UnitOfWork(ABC):
    """
    工作单元接口

    管理数据库事务边界，确保多个 Repository 操作在同一事务中执行。
    解决 PayBillCommand 中 bill_repo.save() + payment_repo.save() + receipt_repo.save()
    的原子性问题。

    SQL:
        BEGIN;
        -- 多个 Repository 操作
        COMMIT;  -- 或 ROLLBACK;
    """

    @abstractmethod
    def commit(self) -> None:
        """
        提交事务

        SQL:
            COMMIT;
        """
        ...

    @abstractmethod
    def rollback(self) -> None:
        """
        回滚事务

        SQL:
            ROLLBACK;
        """
        ...


# ═══════════════════════════════════════════════════
# PRD 6.3 端口：账单来源 / 规则 / 咨询单
# ═══════════════════════════════════════════════════


@dataclass
class IdempotencyRecord:
    """幂等记录（billing 模块本地复用平台 idempotency_records 表）。"""

    actor_id: UUID
    operation: str
    key: str
    request_hash: str
    resource_id: UUID
    response_snapshot: dict


class BillingSourcePort(Protocol):
    """账单数据源抽象（PRD 6.3）。

    隔离本地演示账单源与未来真实财务接口。本地实现读取 fee_bills；
    真实实现可调用外部账务系统。接口失败时抛 ``BillingSourceUnavailable``，
    调用方必须允许保存财务咨询草稿而不猜测金额（R-02）。
    """

    def list_bills(
        self,
        *,
        community_id: str,
        house_id: str | None = None,
        fee_type: str | None = None,
        period: str | None = None,
        status: str | None = None,
    ) -> list[Bill]: ...

    def get_bill(self, *, bill_id: str) -> Bill | None: ...


class BillingIdempotencyPort(Protocol):
    def get(self, actor_id: UUID, operation: str, key: str) -> IdempotencyRecord | None: ...

    def add(self, record: IdempotencyRecord) -> None: ...


class BillingAuditPort(Protocol):
    def add(self, **event: object) -> None: ...


class BillingConfirmationPort(Protocol):
    """原子化消费确认令牌 + 审批（P0 正确性底座）。

    ``create_draft``（写入财务咨询草稿）属于受控写操作，必须在同一 UoW
    内消费审批 + 令牌。``approval_ref`` 缺失时按旧规则只消费令牌（兼容
    未升级调用方，但生产部署时所有受控写工具都应在命令里带上）。
    """

    def consume(
        self,
        *,
        approval_ref: str | None,
        token: str,
        actor_id: UUID,
        action: str,
        parameter_hash: str,
        request_id: str,
    ) -> None: ...


class BillingUnitOfWorkPort(Protocol):
    """Application-facing transaction boundary for billing operations."""

    source: BillingSourcePort
    rules: RuleRepository
    consultations: ConsultationRepository
    idempotency: BillingIdempotencyPort
    audit: BillingAuditPort
    confirmations: BillingConfirmationPort

    def community_code(self, community_id: UUID) -> str: ...

    def commit(self) -> None: ...


class RuleRepository(ABC):
    """计费规则仓储（PRD 6.3）。"""

    @abstractmethod
    def find_effective(
        self, community_id: str, fee_type: str, as_of: str | None = None
    ) -> BillingRule | None:
        """返回当前生效的规则；无有效规则返回 None（声明未知）。"""
        ...

    @abstractmethod
    def save(self, rule: BillingRule) -> BillingRule: ...


class ConsultationRepository(ABC):
    """财务咨询单仓储（PRD 6.3）。"""

    @abstractmethod
    def add(self, ticket: ConsultationTicket) -> ConsultationTicket: ...

    @abstractmethod
    def get(self, consultation_id: str) -> ConsultationTicket | None: ...

    @abstractmethod
    def list_by_actor(self, actor_id: str, community_id: str) -> list[ConsultationTicket]: ...

    @abstractmethod
    def update(
        self, ticket: ConsultationTicket, *, expected_version: int
    ) -> ConsultationTicket: ...
