-- ============================================================================
-- 物业社区管理AI智能体 · 费用查询与智能缴费模块
-- PostgreSQL DDL (V1.0)
-- 适用于: PostgreSQL 14+
-- 遵循项目规范: 字段约束、注释、外键、时间分区
-- ============================================================================

-- 0. 创建 Schema
CREATE SCHEMA IF NOT EXISTS property_fee;
COMMENT ON SCHEMA property_fee IS '物业费用管理模块';

SET search_path TO property_fee;

-- ============================================================================
-- 1. 楼栋信息表
-- ============================================================================
CREATE TABLE community_buildings (
    building_id     VARCHAR(32)     PRIMARY KEY,
    building_name   VARCHAR(64)     NOT NULL,
    building_type   VARCHAR(16)     NOT NULL DEFAULT 'RESIDENTIAL'  CHECK (building_type IN ('RESIDENTIAL', 'COMMERCIAL', 'OFFICE')),
    total_floors    INTEGER         NOT NULL DEFAULT 1               CHECK (total_floors > 0),
    total_units     INTEGER         NOT NULL DEFAULT 0               CHECK (total_units >= 0),
    address         VARCHAR(256),
    status          VARCHAR(16)     NOT NULL DEFAULT 'ACTIVE'        CHECK (status IN ('ACTIVE', 'INACTIVE', 'MAINTENANCE')),
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON TABLE community_buildings IS '社区楼栋信息';
COMMENT ON COLUMN community_buildings.building_id IS '楼栋唯一标识';
COMMENT ON COLUMN community_buildings.building_name IS '楼栋名称，如 1号楼、商铺A栋';
COMMENT ON COLUMN community_buildings.building_type IS '楼栋类型: RESIDENTIAL-住宅, COMMERCIAL-商铺, OFFICE-办公';
COMMENT ON COLUMN community_buildings.total_floors IS '总楼层数';
COMMENT ON COLUMN community_buildings.total_units IS '总户数/单元数';
COMMENT ON COLUMN community_buildings.status IS '状态: ACTIVE-运营中, INACTIVE-停用, MAINTENANCE-维护中';

-- ============================================================================
-- 2. 房号信息表
-- ============================================================================
CREATE TABLE community_rooms (
    room_id         VARCHAR(32)     PRIMARY KEY,
    building_id     VARCHAR(32)     NOT NULL,
    room_number     VARCHAR(16)     NOT NULL,
    room_area       NUMERIC(10,2)   NOT NULL DEFAULT 0               CHECK (room_area >= 0),
    property_fee_rate NUMERIC(10,4) NOT NULL DEFAULT 0               CHECK (property_fee_rate >= 0),  -- 物业费单价(元/㎡·月)
    parking_spots   INTEGER         NOT NULL DEFAULT 0               CHECK (parking_spots >= 0),
    parking_fee_rate NUMERIC(10,2)  NOT NULL DEFAULT 0               CHECK (parking_fee_rate >= 0),    -- 车位费单价(元/个·月)
    status          VARCHAR(16)     NOT NULL DEFAULT 'OCCUPIED'      CHECK (status IN ('OCCUPIED', 'VACANT', 'DECORATING')),
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_room_building FOREIGN KEY (building_id) REFERENCES community_buildings(building_id),
    CONSTRAINT uq_room_building UNIQUE (building_id, room_number)
);
COMMENT ON TABLE community_rooms IS '社区房号信息';
COMMENT ON COLUMN community_rooms.room_id IS '房号唯一标识';
COMMENT ON COLUMN community_rooms.room_number IS '房号，如 201、301';
COMMENT ON COLUMN community_rooms.room_area IS '建筑面积(㎡)';
COMMENT ON COLUMN community_rooms.property_fee_rate IS '物业费单价(元/㎡·月)';
COMMENT ON COLUMN community_rooms.parking_spots IS '绑定车位数量';
COMMENT ON COLUMN community_rooms.parking_fee_rate IS '车位费单价(元/个·月)';
COMMENT ON COLUMN community_rooms.status IS '状态: OCCUPIED-已入住, VACANT-空置, DECORATING-装修中';

-- ============================================================================
-- 3. 用户表
-- ============================================================================
CREATE TABLE sys_users (
    user_id         VARCHAR(32)     PRIMARY KEY,
    user_name       VARCHAR(64)     NOT NULL,
    role            VARCHAR(16)     NOT NULL DEFAULT 'owner'         CHECK (role IN ('owner', 'staff', 'admin')),
    building_id     VARCHAR(32),
    room_id         VARCHAR(32),
    phone           VARCHAR(20),
    email           VARCHAR(128),
    id_card         VARCHAR(18),
    face_img_url    VARCHAR(512),
    status          VARCHAR(16)     NOT NULL DEFAULT 'ACTIVE'        CHECK (status IN ('ACTIVE', 'INACTIVE', 'FROZEN')),
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_user_building FOREIGN KEY (building_id) REFERENCES community_buildings(building_id),
    CONSTRAINT fk_user_room FOREIGN KEY (room_id) REFERENCES community_rooms(room_id)
);
COMMENT ON TABLE sys_users IS '系统用户表（业主/物业员工/管理员）';
COMMENT ON COLUMN sys_users.user_id IS '用户唯一标识，格式: user_{序号}/staff_{序号}/admin_{序号}';
COMMENT ON COLUMN sys_users.role IS '角色: owner-业主, staff-物业员工, admin-管理员';
COMMENT ON COLUMN sys_users.face_img_url IS '人脸识别照片URL';
COMMENT ON COLUMN sys_users.status IS '状态: ACTIVE-正常, INACTIVE-停用, FROZEN-冻结';

-- ============================================================================
-- 4. 账单主表 (按月分区)
-- ============================================================================
CREATE TABLE fee_bills (
    bill_id         VARCHAR(32)     PRIMARY KEY,
    user_id         VARCHAR(32)     NOT NULL,
    room_id         VARCHAR(32)     NOT NULL,
    bill_period     VARCHAR(7)      NOT NULL,                         -- 账期 YYYY-MM
    property_fee    NUMERIC(10,2)   NOT NULL DEFAULT 0                CHECK (property_fee >= 0),
    utility_fee     NUMERIC(10,2)   NOT NULL DEFAULT 0                CHECK (utility_fee >= 0),   -- 公摊水电费
    parking_fee     NUMERIC(10,2)   NOT NULL DEFAULT 0                CHECK (parking_fee >= 0),
    late_fee        NUMERIC(10,2)   NOT NULL DEFAULT 0                CHECK (late_fee >= 0),      -- 滞纳金
    total_amount    NUMERIC(10,2)   NOT NULL DEFAULT 0                CHECK (total_amount >= 0),
    due_date        DATE            NOT NULL,                         -- 最迟缴费日
    status          VARCHAR(16)     NOT NULL DEFAULT 'UNPAID'         CHECK (status IN ('UNPAID', 'OVERDUE', 'PAID', 'CANCELLED')),
    payment_time    TIMESTAMP,
    receipt_no      VARCHAR(32),
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_bill_user FOREIGN KEY (user_id) REFERENCES sys_users(user_id),
    CONSTRAINT fk_bill_room FOREIGN KEY (room_id) REFERENCES community_rooms(room_id),
    CONSTRAINT uq_bill_period UNIQUE (user_id, bill_period)
) PARTITION BY RANGE (due_date);
COMMENT ON TABLE fee_bills IS '物业费用账单主表（按月分区）';
COMMENT ON COLUMN fee_bills.bill_id IS '账单唯一标识，格式: bill_{序号}';
COMMENT ON COLUMN fee_bills.bill_period IS '账期，格式 YYYY-MM';
COMMENT ON COLUMN fee_bills.utility_fee IS '公摊水电费';
COMMENT ON COLUMN fee_bills.late_fee IS '滞纳金，逾期后按日计算';
COMMENT ON COLUMN fee_bills.status IS '状态: UNPAID-未到期, OVERDUE-已逾期, PAID-已缴费, CANCELLED-已作废';
COMMENT ON COLUMN fee_bills.receipt_no IS '关联电子票据单号';

-- 创建默认分区 (2026-2027)
CREATE TABLE fee_bills_2026 PARTITION OF fee_bills
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE fee_bills_2027 PARTITION OF fee_bills
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE fee_bills_default PARTITION OF fee_bills DEFAULT;

-- ============================================================================
-- 5. 缴费记录表
-- ============================================================================
CREATE TABLE fee_payments (
    payment_id      VARCHAR(32)     PRIMARY KEY,
    bill_id         VARCHAR(32)     NOT NULL,
    user_id         VARCHAR(32)     NOT NULL,
    pay_amount      NUMERIC(10,2)   NOT NULL                               CHECK (pay_amount > 0),
    pay_method      VARCHAR(16)     NOT NULL DEFAULT 'WECHAT'              CHECK (pay_method IN ('WECHAT', 'ALIPAY', 'BANK_CARD', 'CASH', 'OFFLINE')),
    pay_status      VARCHAR(16)     NOT NULL DEFAULT 'SUCCESS'             CHECK (pay_status IN ('PENDING', 'SUCCESS', 'FAILED', 'REFUNDED')),
    transaction_id  VARCHAR(64),
    receipt_no      VARCHAR(32),
    paid_at         TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_payment_bill FOREIGN KEY (bill_id) REFERENCES fee_bills(bill_id),
    CONSTRAINT fk_payment_user FOREIGN KEY (user_id) REFERENCES sys_users(user_id)
);
COMMENT ON TABLE fee_payments IS '缴费记录表';
COMMENT ON COLUMN fee_payments.pay_method IS '支付方式: WECHAT-微信, ALIPAY-支付宝, BANK_CARD-银行卡, CASH-现金, OFFLINE-线下';
COMMENT ON COLUMN fee_payments.transaction_id IS '第三方支付流水号';
COMMENT ON COLUMN fee_payments.paid_at IS '实际支付时间';

-- ============================================================================
-- 6. 电子票据表
-- ============================================================================
CREATE TABLE fee_receipts (
    receipt_no      VARCHAR(32)     PRIMARY KEY,
    bill_id         VARCHAR(32)     NOT NULL,
    user_id         VARCHAR(32)     NOT NULL,
    payment_id      VARCHAR(32)     NOT NULL,
    period          VARCHAR(7)      NOT NULL,
    property_fee    NUMERIC(10,2)   NOT NULL DEFAULT 0,
    utility_fee     NUMERIC(10,2)   NOT NULL DEFAULT 0,
    parking_fee     NUMERIC(10,2)   NOT NULL DEFAULT 0,
    late_fee        NUMERIC(10,2)   NOT NULL DEFAULT 0,
    total_amount    NUMERIC(10,2)   NOT NULL DEFAULT 0,
    issue_time      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_valid        BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_receipt_bill FOREIGN KEY (bill_id) REFERENCES fee_bills(bill_id),
    CONSTRAINT fk_receipt_user FOREIGN KEY (user_id) REFERENCES sys_users(user_id),
    CONSTRAINT fk_receipt_payment FOREIGN KEY (payment_id) REFERENCES fee_payments(payment_id)
);
COMMENT ON TABLE fee_receipts IS '电子票据表';
COMMENT ON COLUMN fee_receipts.receipt_no IS '票据编号，格式: REC_{YYYYMMDD}_{用户序号}';
COMMENT ON COLUMN fee_receipts.is_valid IS '是否有效（作废票据标记为 FALSE）';

-- ============================================================================
-- 索引
-- ============================================================================
CREATE INDEX idx_bills_user_id ON fee_bills(user_id);
CREATE INDEX idx_bills_status ON fee_bills(status);
CREATE INDEX idx_bills_due_date ON fee_bills(due_date);
CREATE INDEX idx_bills_period ON fee_bills(bill_period);
CREATE INDEX idx_payments_user_id ON fee_payments(user_id);
CREATE INDEX idx_payments_bill_id ON fee_payments(bill_id);
CREATE INDEX idx_receipts_user_id ON fee_receipts(user_id);
CREATE INDEX idx_receipts_bill_id ON fee_receipts(bill_id);
CREATE INDEX idx_users_role ON sys_users(role);
CREATE INDEX idx_rooms_building ON community_rooms(building_id);

-- ============================================================================
-- 触发器: 自动更新 updated_at
-- ============================================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_buildings_updated_at BEFORE UPDATE ON community_buildings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_rooms_updated_at BEFORE UPDATE ON community_rooms
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON sys_users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_bills_updated_at BEFORE UPDATE ON fee_bills
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();