-- ============================================================================
-- 物业社区管理AI智能体 · 费用查询与智能缴费模块
-- 种子数据 (Seed Data)
-- ============================================================================

-- 1. 楼栋数据
INSERT INTO community_buildings (building_id, building_name, building_type, total_floors, total_units, address, status) VALUES
('BLDG_001', '1号楼', 'RESIDENTIAL', 18, 72, '幸福小区1号', 'ACTIVE'),
('BLDG_002', '2号楼', 'RESIDENTIAL', 18, 72, '幸福小区2号', 'ACTIVE'),
('BLDG_003', '商铺A栋', 'COMMERCIAL', 3, 12, '幸福小区商业街A', 'ACTIVE');

-- 2. 房号数据
INSERT INTO community_rooms (room_id, building_id, room_number, room_area, property_fee_rate, parking_spots, parking_fee_rate, status) VALUES
('ROOM_001', 'BLDG_001', '201', 100.00, 3.00, 1, 150.00, 'OCCUPIED'),
('ROOM_002', 'BLDG_001', '202', 100.00, 3.00, 1, 150.00, 'OCCUPIED'),
('ROOM_003', 'BLDG_001', '301', 120.00, 3.00, 1, 150.00, 'OCCUPIED'),
('ROOM_004', 'BLDG_002', '101', 100.00, 3.00, 0, 0.00, 'OCCUPIED'),
('ROOM_005', 'BLDG_003', 'S01', 80.00, 5.00, 0, 0.00, 'OCCUPIED');

-- 3. 用户数据
INSERT INTO sys_users (user_id, user_name, role, building_id, room_id, phone, status) VALUES
('user_101', '张三', 'owner', 'BLDG_001', 'ROOM_001', '138****6789', 'ACTIVE'),
('user_102', '李四', 'owner', 'BLDG_001', 'ROOM_002', '138****6790', 'ACTIVE'),
('user_201', '王五', 'owner', 'BLDG_002', 'ROOM_004', '138****6791', 'ACTIVE'),
('user_301', '商铺赵六', 'owner', 'BLDG_003', 'ROOM_005', '138****6792', 'ACTIVE'),
('staff_201', '李管家', 'staff', 'BLDG_001', NULL, '139****8901', 'ACTIVE'),
('staff_202', '刘管家', 'staff', 'BLDG_002', NULL, '139****8902', 'ACTIVE'),
('admin_301', '王经理', 'admin', NULL, NULL, '137****0123', 'ACTIVE');

-- 4. 账单数据
-- 用户 user_101 (张三) 的账单
INSERT INTO fee_bills (bill_id, user_id, room_id, bill_period, property_fee, utility_fee, parking_fee, late_fee, total_amount, due_date, status) VALUES
('bill_001', 'user_101', 'ROOM_001', '2026-07', 300.00, 45.50, 150.00, 0.00,   495.50, '2026-08-05', 'UNPAID'),
('bill_002', 'user_101', 'ROOM_001', '2026-06', 300.00, 30.00, 150.00, 15.00,  495.00, '2026-07-05', 'OVERDUE'),
('bill_003', 'user_101', 'ROOM_001', '2026-05', 300.00, 30.00, 150.00, 0.00,   480.00, '2026-06-05', 'PAID');

-- 用户 user_102 (李四) 的账单
INSERT INTO fee_bills (bill_id, user_id, room_id, bill_period, property_fee, utility_fee, parking_fee, late_fee, total_amount, due_date, status) VALUES
('bill_004', 'user_102', 'ROOM_002', '2026-07', 300.00, 40.00, 150.00, 0.00,   490.00, '2026-08-05', 'UNPAID'),
('bill_005', 'user_102', 'ROOM_002', '2026-06', 300.00, 35.00, 150.00, 0.00,   485.00, '2026-07-05', 'PAID');

-- 用户 user_201 (王五) 的账单
INSERT INTO fee_bills (bill_id, user_id, room_id, bill_period, property_fee, utility_fee, parking_fee, late_fee, total_amount, due_date, status) VALUES
('bill_006', 'user_201', 'ROOM_004', '2026-07', 300.00, 35.00, 0.00,   0.00,   335.00, '2026-08-05', 'UNPAID'),
('bill_007', 'user_201', 'ROOM_004', '2026-06', 300.00, 28.00, 0.00,   10.00,  338.00, '2026-07-05', 'OVERDUE');

-- 用户 user_301 (商铺) 的账单
INSERT INTO fee_bills (bill_id, user_id, room_id, bill_period, property_fee, utility_fee, parking_fee, late_fee, total_amount, due_date, status) VALUES
('bill_008', 'user_301', 'ROOM_005', '2026-07', 400.00, 80.00, 0.00,   0.00,   480.00, '2026-08-05', 'UNPAID');

-- 5. 缴费记录 (bill_003 已缴费)
INSERT INTO fee_payments (payment_id, bill_id, user_id, pay_amount, pay_method, pay_status, transaction_id, receipt_no, paid_at) VALUES
('pay_001', 'bill_003', 'user_101', 480.00, 'WECHAT', 'SUCCESS', 'TXN_20260510_001', 'REC_20260510_101', '2026-05-10 14:32:00'),
('pay_002', 'bill_005', 'user_102', 485.00, 'ALIPAY', 'SUCCESS', 'TXN_20260710_002', 'REC_20260710_102', '2026-07-10 09:15:00');

-- 6. 电子票据
INSERT INTO fee_receipts (receipt_no, bill_id, user_id, payment_id, period, property_fee, utility_fee, parking_fee, late_fee, total_amount, issue_time) VALUES
('REC_20260510_101', 'bill_003', 'user_101', 'pay_001', '2026-05', 300.00, 30.00, 150.00, 0.00, 480.00, '2026-05-10 14:32:05'),
('REC_20260710_102', 'bill_005', 'user_102', 'pay_002', '2026-06', 300.00, 35.00, 150.00, 0.00, 485.00, '2026-07-10 09:15:03');