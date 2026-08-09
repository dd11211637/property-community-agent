"""
domain/enums.py     枚举定义

领域枚举，对应数据库中的 CHECK 约束和字段默认值。
每个枚举类标注对应的 SQL CHECK 约束和查询示例。

────────────────────────────────────────────────────────
8 个枚举:
────────────────────────────────────────────────────────
  BillStatus     账单状态    → CHECK (status IN ('UNPAID','OVERDUE','PAID','CANCELLED'))
  UserRole       用户角色    → CHECK (role IN ('owner','staff','admin'))
  ReminderLevel  催缴层级    → 应用层计算，基于逾期天数
  PayMethod      支付方式    → CHECK (pay_method IN ('WECHAT','ALIPAY','BANK_CARD','CASH','OFFLINE'))
  PayStatus      支付状态    → CHECK (pay_status IN ('PENDING','SUCCESS','FAILED','REFUNDED'))
  BuildingType   楼栋类型    → CHECK (building_type IN ('RESIDENTIAL','COMMERCIAL','OFFICE'))
  RoomStatus     房号状态    → CHECK (status IN ('OCCUPIED','VACANT','DECORATING'))
  BuildingStatus 楼栋状态    → CHECK (status IN ('ACTIVE','INACTIVE','MAINTENANCE'))
  UserStatus     用户状态    → CHECK (status IN ('ACTIVE','INACTIVE','FROZEN'))

────────────────────────────────────────────────────────
调用链:
────────────────────────────────────────────────────────
  Bill.status = BillStatus.UNPAID
    → state_machine.py: transition_to(bill, PAID)
      → SQL: UPDATE fee_bills SET status='PAID' WHERE bill_id=:id;

  User.role = UserRole.OWNER
    → queries.py: GetBillsByRole.execute(user_id, role)
      → SQL: SELECT * FROM fee_bills WHERE user_id = :user_id;  -- owner
"""
from __future__ import annotations
from enum import Enum


# ═══════════════════════════════════════════════════════════════
# FeeType · 费用类型（PRD 6.3 筛选维度）
# ═══════════════════════════════════════════════════════════════

class FeeType(str, Enum):
    """账单费用类型维度。"""

    PROPERTY = "PROPERTY"   # 物业费
    UTILITY = "UTILITY"     # 公摊水电费
    PARKING = "PARKING"     # 车位费
    LATE_FEE = "LATE_FEE"   # 滞纳金
    MIXED = "MIXED"         # 混合/未分类


# ═══════════════════════════════════════════════════════════════
# ConsultationStatus · 财务咨询单状态（PRD 6.3）
# ═══════════════════════════════════════════════════════════════

class ConsultationStatus(str, Enum):
    """财务咨询单状态机节点。"""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PROCESSING = "PROCESSING"
    ANSWERED = "ANSWERED"
    RESOLVED = "RESOLVED"
    APPEALED = "APPEALED"


# ═══════════════════════════════════════════════════════════════
# BillStatus · 账单状态
# ═══════════════════════════════════════════════════════════════

class BillStatus(str, Enum):
    """
    账单状态枚举

    对应 SQL CHECK 约束:
        status VARCHAR(16) NOT NULL DEFAULT 'UNPAID'
            CHECK (status IN ('UNPAID', 'OVERDUE', 'PAID', 'CANCELLED'))

    状态流转: UNPAID → OVERDUE (自动) → PAID (人工) → CANCELLED (管理员)

    SQL 查询示例:
        SELECT * FROM fee_bills WHERE status = 'UNPAID';
        SELECT COUNT(*) FROM fee_bills WHERE status = 'OVERDUE';
        UPDATE fee_bills SET status='OVERDUE' WHERE status='UNPAID' AND due_date < CURRENT_DATE;
    """
    UNPAID = "UNPAID"           # 未到期
    OVERDUE = "OVERDUE"         # 已逾期
    PAID = "PAID"               # 已缴费
    CANCELLED = "CANCELLED"     # 已作废


# ═══════════════════════════════════════════════════════════════
# UserRole · 用户角色
# ═══════════════════════════════════════════════════════════════

class UserRole(str, Enum):
    """
    用户角色

    对应 SQL CHECK 约束:
        role VARCHAR(16) NOT NULL DEFAULT 'owner'
            CHECK (role IN ('owner', 'staff', 'admin'))

    权限控制:
      - owner: 查询自己的账单
      - staff: 查询所管楼栋的账单
      - admin: 查询所有账单

    SQL 查询示例:
        SELECT * FROM sys_users WHERE role = 'owner';
        SELECT * FROM sys_users WHERE role = 'staff';
    """
    OWNER = "owner"              # 业主用户
    PROPERTY_STAFF = "staff"      # 物业工作人员
    COMMUNITY_ADMIN = "admin"     # 社区管理员


# ═══════════════════════════════════════════════════════════════
# ReminderLevel · 催缴提醒层级
# ═══════════════════════════════════════════════════════════════

class ReminderLevel(str, Enum):
    """
    催缴提醒层级

    由 business_rules.py: determine_reminder_level() 计算。

    对应 SQL:
        SELECT CASE
            WHEN status = 'PAID' THEN 'gentle'
            WHEN status = 'UNPAID' AND CURRENT_DATE <= due_date THEN 'gentle'
            WHEN status = 'OVERDUE' AND (CURRENT_DATE - due_date) <= 30 THEN 'short'
            WHEN status = 'OVERDUE' AND (CURRENT_DATE - due_date) > 30 THEN 'long'
        END AS reminder_level
        FROM fee_bills WHERE bill_id = :bill_id;
    """
    GENTLE = "gentle"              # 未到期，温和提醒
    SHORT_OVERDUE = "short"        # 逾期 ≤30天，快捷缴费引导
    LONG_OVERDUE = "long"          # 逾期 >30天，警示 + 管家介入


# ═══════════════════════════════════════════════════════════════
# PayMethod · 支付方式
# ═══════════════════════════════════════════════════════════════

class PayMethod(str, Enum):
    """
    支付方式

    对应 SQL CHECK 约束:
        pay_method VARCHAR(16) NOT NULL DEFAULT 'WECHAT'
            CHECK (pay_method IN ('WECHAT', 'ALIPAY', 'BANK_CARD', 'CASH', 'OFFLINE'))

    SQL 查询示例:
        SELECT pay_method, COUNT(*) FROM fee_payments GROUP BY pay_method;
        INSERT INTO fee_payments (..., pay_method, ...) VALUES (..., 'WECHAT', ...);
    """
    WECHAT = "WECHAT"
    ALIPAY = "ALIPAY"
    BANK_CARD = "BANK_CARD"
    CASH = "CASH"
    OFFLINE = "OFFLINE"


# ═══════════════════════════════════════════════════════════════
# PayStatus · 支付状态
# ═══════════════════════════════════════════════════════════════

class PayStatus(str, Enum):
    """
    支付状态

    对应 SQL CHECK 约束:
        pay_status VARCHAR(16) NOT NULL DEFAULT 'SUCCESS'
            CHECK (pay_status IN ('PENDING', 'SUCCESS', 'FAILED', 'REFUNDED'))

    SQL 查询示例:
        SELECT * FROM fee_payments WHERE pay_status = 'SUCCESS';
        UPDATE fee_payments SET pay_status='REFUNDED' WHERE bill_id=:bill_id;
    """
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


# ═══════════════════════════════════════════════════════════════
# BuildingType · 楼栋类型
# ═══════════════════════════════════════════════════════════════

class BuildingType(str, Enum):
    """
    楼栋类型

    对应 SQL CHECK 约束:
        building_type VARCHAR(16) NOT NULL DEFAULT 'RESIDENTIAL'
            CHECK (building_type IN ('RESIDENTIAL', 'COMMERCIAL', 'OFFICE'))

    SQL 查询示例:
        SELECT * FROM community_buildings WHERE building_type = 'RESIDENTIAL';
    """
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    OFFICE = "OFFICE"


# ═══════════════════════════════════════════════════════════════
# RoomStatus · 房号状态
# ═══════════════════════════════════════════════════════════════

class RoomStatus(str, Enum):
    """
    房号状态

    对应 SQL CHECK 约束:
        status VARCHAR(16) NOT NULL DEFAULT 'OCCUPIED'
            CHECK (status IN ('OCCUPIED', 'VACANT', 'DECORATING'))

    SQL 查询示例:
        SELECT * FROM community_rooms WHERE status = 'OCCUPIED';
    """
    OCCUPIED = "OCCUPIED"
    VACANT = "VACANT"
    DECORATING = "DECORATING"


# ═══════════════════════════════════════════════════════════════
# BuildingStatus · 楼栋状态
# ═══════════════════════════════════════════════════════════════

class BuildingStatus(str, Enum):
    """
    楼栋状态

    对应 SQL CHECK 约束:
        status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
            CHECK (status IN ('ACTIVE', 'INACTIVE', 'MAINTENANCE'))

    SQL 查询示例:
        SELECT * FROM community_buildings WHERE status = 'ACTIVE';
    """
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MAINTENANCE = "MAINTENANCE"


# ═══════════════════════════════════════════════════════════════
# UserStatus · 用户状态
# ═══════════════════════════════════════════════════════════════

class UserStatus(str, Enum):
    """
    用户状态

    对应 SQL CHECK 约束:
        status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
            CHECK (status IN ('ACTIVE', 'INACTIVE', 'FROZEN'))

    SQL 查询示例:
        SELECT * FROM sys_users WHERE status = 'ACTIVE';
        SELECT user_id FROM sys_users WHERE room_id=:room_id AND status='ACTIVE' AND role='owner';
    """
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    FROZEN = "FROZEN"