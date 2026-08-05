@echo off
chcp 65001 >nul
echo ============================================
echo  物业社区管理AI智能体 - 费用查询与智能缴费
echo  (DDD 分层架构版本)
echo ============================================
echo.
echo 项目结构:
echo   src/property_agent/billing/
echo   ├── domain/           实体、状态机、业务规则
echo   ├── application/      用例、命令、查询、Port
echo   ├── adapters/         HTTP API、Agent 工具
echo   └── infrastructure/   数据库和外部系统实现
echo.
echo 数据库: 默认 SQLite (backend/data/property_fee.db)
echo 切换 PostgreSQL: set DB_URL=postgresql://user:pass@localhost:5432/property_fee
echo.
echo [1/3] 安装 Python 依赖...
cd /d "%~dp0backend"
pip install -r requirements.txt -q
echo.
echo [2/3] 初始化数据库表...
python -c "import sys; sys.path.insert(0, '../src'); from property_agent.billing.infrastructure.orm_models import Base; from property_agent.billing.infrastructure.database import engine; Base.metadata.create_all(bind=engine); print('[OK] 6 张表已就绪')"
echo.
echo [3/3] 导入种子数据 (首次运行自动注入)...
python -c "import sys; sys.path.insert(0, '../src'); from property_agent.billing.infrastructure.database import SessionLocal; from property_agent.billing.infrastructure.orm_models import BuildingModel, RoomModel, UserModel, BillModel, PaymentModel, ReceiptModel; from datetime import date, datetime; db = SessionLocal(); 
count = db.query(BuildingModel).count();
if count == 0:
    db.add_all([
        BuildingModel(building_id='BLDG_001', building_name='1号楼', building_type='RESIDENTIAL', total_floors=18, total_units=72, address='幸福小区1号', status='ACTIVE'),
        BuildingModel(building_id='BLDG_002', building_name='2号楼', building_type='RESIDENTIAL', total_floors=18, total_units=72, address='幸福小区2号', status='ACTIVE'),
        BuildingModel(building_id='BLDG_003', building_name='商铺A栋', building_type='COMMERCIAL', total_floors=3, total_units=12, address='幸福小区商业街A', status='ACTIVE'),
    ]);
    db.add_all([
        RoomModel(room_id='ROOM_001', building_id='BLDG_001', room_number='201', room_area=100.00, property_fee_rate=3.00, parking_spots=1, parking_fee_rate=150.00, status='OCCUPIED'),
        RoomModel(room_id='ROOM_002', building_id='BLDG_001', room_number='202', room_area=100.00, property_fee_rate=3.00, parking_spots=1, parking_fee_rate=150.00, status='OCCUPIED'),
        RoomModel(room_id='ROOM_003', building_id='BLDG_001', room_number='301', room_area=120.00, property_fee_rate=3.00, parking_spots=1, parking_fee_rate=150.00, status='OCCUPIED'),
        RoomModel(room_id='ROOM_004', building_id='BLDG_002', room_number='101', room_area=100.00, property_fee_rate=3.00, parking_spots=0, parking_fee_rate=0.00, status='OCCUPIED'),
        RoomModel(room_id='ROOM_005', building_id='BLDG_003', room_number='S01', room_area=80.00, property_fee_rate=5.00, parking_spots=0, parking_fee_rate=0.00, status='OCCUPIED'),
    ]);
    db.add_all([
        UserModel(user_id='user_101', user_name='张三', role='owner', building_id='BLDG_001', room_id='ROOM_001', phone='138****6789', status='ACTIVE'),
        UserModel(user_id='user_102', user_name='李四', role='owner', building_id='BLDG_001', room_id='ROOM_002', phone='138****6790', status='ACTIVE'),
        UserModel(user_id='user_201', user_name='王五', role='owner', building_id='BLDG_002', room_id='ROOM_004', phone='138****6791', status='ACTIVE'),
        UserModel(user_id='user_301', user_name='商铺赵六', role='owner', building_id='BLDG_003', room_id='ROOM_005', phone='138****6792', status='ACTIVE'),
        UserModel(user_id='staff_201', user_name='李管家', role='staff', building_id='BLDG_001', phone='139****8901', status='ACTIVE'),
        UserModel(user_id='staff_202', user_name='刘管家', role='staff', building_id='BLDG_002', phone='139****8902', status='ACTIVE'),
        UserModel(user_id='admin_301', user_name='王经理', role='admin', phone='137****0123', status='ACTIVE'),
    ]);
    db.add_all([
        BillModel(bill_id='bill_001', user_id='user_101', room_id='ROOM_001', bill_period='2026-07', property_fee=300.00, utility_fee=45.50, parking_fee=150.00, late_fee=0.00, total_amount=495.50, due_date=date(2026,8,5), status='UNPAID'),
        BillModel(bill_id='bill_002', user_id='user_101', room_id='ROOM_001', bill_period='2026-06', property_fee=300.00, utility_fee=30.00, parking_fee=150.00, late_fee=15.00, total_amount=495.00, due_date=date(2026,7,5), status='OVERDUE'),
        BillModel(bill_id='bill_003', user_id='user_101', room_id='ROOM_001', bill_period='2026-05', property_fee=300.00, utility_fee=30.00, parking_fee=150.00, late_fee=0.00, total_amount=480.00, due_date=date(2026,6,5), status='PAID', payment_time=datetime(2026,5,10,14,32,0), receipt_no='REC_20260510_101'),
        BillModel(bill_id='bill_004', user_id='user_102', room_id='ROOM_002', bill_period='2026-07', property_fee=300.00, utility_fee=40.00, parking_fee=150.00, late_fee=0.00, total_amount=490.00, due_date=date(2026,8,5), status='UNPAID'),
        BillModel(bill_id='bill_005', user_id='user_102', room_id='ROOM_002', bill_period='2026-06', property_fee=300.00, utility_fee=35.00, parking_fee=150.00, late_fee=0.00, total_amount=485.00, due_date=date(2026,7,5), status='PAID', payment_time=datetime(2026,7,10,9,15,0), receipt_no='REC_20260710_102'),
        BillModel(bill_id='bill_006', user_id='user_201', room_id='ROOM_004', bill_period='2026-07', property_fee=300.00, utility_fee=35.00, parking_fee=0.00, late_fee=0.00, total_amount=335.00, due_date=date(2026,8,5), status='UNPAID'),
        BillModel(bill_id='bill_007', user_id='user_201', room_id='ROOM_004', bill_period='2026-06', property_fee=300.00, utility_fee=28.00, parking_fee=0.00, late_fee=10.00, total_amount=338.00, due_date=date(2026,7,5), status='OVERDUE'),
        BillModel(bill_id='bill_008', user_id='user_301', room_id='ROOM_005', bill_period='2026-07', property_fee=400.00, utility_fee=80.00, parking_fee=0.00, late_fee=0.00, total_amount=480.00, due_date=date(2026,8,5), status='UNPAID'),
    ]);
    db.add_all([
        PaymentModel(payment_id='pay_001', bill_id='bill_003', user_id='user_101', pay_amount=480.00, pay_method='WECHAT', pay_status='SUCCESS', transaction_id='TXN_20260510_001', receipt_no='REC_20260510_101', paid_at=datetime(2026,5,10,14,32,0)),
        PaymentModel(payment_id='pay_002', bill_id='bill_005', user_id='user_102', pay_amount=485.00, pay_method='ALIPAY', pay_status='SUCCESS', transaction_id='TXN_20260710_002', receipt_no='REC_20260710_102', paid_at=datetime(2026,7,10,9,15,0)),
    ]);
    db.add_all([
        ReceiptModel(receipt_no='REC_20260510_101', bill_id='bill_003', user_id='user_101', payment_id='pay_001', period='2026-05', property_fee=300.00, utility_fee=30.00, parking_fee=150.00, late_fee=0.00, total_amount=480.00, issue_time=datetime(2026,5,10,14,32,5)),
        ReceiptModel(receipt_no='REC_20260710_102', bill_id='bill_005', user_id='user_102', payment_id='pay_002', period='2026-06', property_fee=300.00, utility_fee=35.00, parking_fee=150.00, late_fee=0.00, total_amount=485.00, issue_time=datetime(2026,7,10,9,15,3)),
    ]);
    db.commit();
    print('[OK] 种子数据已注入 (3 楼栋 + 5 房号 + 7 用户 + 8 账单 + 2 支付记录 + 2 票据)');
else:
    print('[OK] 数据已存在，跳过种子注入 (' + str(count) + ' 栋楼)');
db.close()"
echo.
echo ============================================
echo  启动 FastAPI 服务 (端口 8080)...
echo  打开浏览器: http://localhost:8080
echo  Swagger 文档: http://localhost:8080/docs
echo  按 Ctrl+C 停止服务
echo ============================================
echo.
python -m uvicorn main:app --host 0.0.0.0 --port 8080 --reload
pause