"""
infrastructure/    基础设施层

数据库连接、ORM 模型、仓储实现、外部服务（LLM/支付网关）实现。
所有具体实现放在此层，通过 application/ports.py 定义的接口暴露给上层。

────────────────────────────────────────────────────────
调用链（以缴费为例）:
────────────────────────────────────────────────────────
    HTTP POST /api/bills/pay {"bill_id": "bill_202607_101", "user_id": "user_101"}
      → adapters/api/routes.py: pay_bill()
        → application/use_cases.py: PaymentUseCase.pay_single()
          → application/commands.py: PayBillCommand.execute()
            → infrastructure/repositories.py: SqlAlchemyBillRepository.find_by_id()
              → infrastructure/orm_models.py: BillModel (SQLAlchemy ORM)
                → infrastructure/database.py: SessionLocal (engine)
                  → SQL:  SELECT * FROM fee_bills WHERE bill_id = :bill_id;
            → infrastructure/payment_gateway.py: MockPaymentGateway.process_payment()
              → SQL:  INSERT INTO fee_payments (...) VALUES (...);
            → infrastructure/repositories.py: SqlAlchemyBillRepository.save()
              → SQL:  UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:id;
            → infrastructure/repositories.py: SqlAlchemyPaymentRepository.save()
              → SQL:  INSERT INTO fee_payments (...) VALUES (...);
            → infrastructure/repositories.py: SqlAlchemyReceiptRepository.save()
              → SQL:  INSERT INTO fee_receipts (...) VALUES (...);
            → infrastructure/repositories.py: SqlAlchemyUnitOfWork.commit()
              → SQL:  COMMIT;

────────────────────────────────────────────────────────
文件与职责:
────────────────────────────────────────────────────────
  database.py
    - 数据库连接管理 (SQLAlchemy Engine, SessionLocal)
    - 支持 SQLite (开发) 和 PostgreSQL (生产) 通过 DB_URL 环境变量切换
    - 提供 get_db() FastAPI 依赖注入 和 get_db_session() 直接调用
    - SQL: CONNECT TO property_fee;
    - SQL: SESSION BEGIN / COMMIT / ROLLBACK;

  orm_models.py
    - SQLAlchemy ORM 模型 (6 张表)
    - BuildingModel, RoomModel, UserModel, BillModel, PaymentModel, ReceiptModel
    - 每个模型标注完整的 DDL CREATE TABLE + 索引 + 外键 + CHECK 约束
    - 与 domain/entities.py 中的领域实体一一对应
    - 通过 repositories.py 中的映射函数转换为领域实体

  repositories.py
    - 实现 application/ports.py 中定义的所有仓储接口
    - SqlAlchemyBillRepository     → BillRepository     (7 个方法)
    - SqlAlchemyUserRepository     → UserRepository     (2 个方法)
    - SqlAlchemyPaymentRepository  → PaymentRepository  (5 个方法)
    - SqlAlchemyReceiptRepository  → ReceiptRepository  (3 个方法)
    - SqlAlchemyBuildingRepository → BuildingRepository (2 个方法)
    - SqlAlchemyRoomRepository     → RoomRepository     (4 个方法)
    - SqlAlchemyUnitOfWork         → UnitOfWork         (2 个方法)
    - 每个方法标注等价 SQL 语句
    - 包含 ORM 模型 → 领域实体的映射函数

  llm_client.py
    - 实现 application/ports.py 中的 LLMClient 接口
    - HttpLLMClient: 支持 OpenAI / Qwen / DeepSeek 三种 LLM 提供商
    - 自动检测环境变量: OPENAI_API_KEY / QWEN_API_KEY / DEEPSEEK_API_KEY
    - 无 API Key 时降级为内置模板 (_fallback_interpretation)
    - 包含 System Prompt (AI 管家小智) 和 User Prompt 构建逻辑
    - SQL: SELECT * FROM fee_bills WHERE bill_id = :bill_id;
    - SQL: SELECT user_name FROM sys_users WHERE user_id = :user_id;

  payment_gateway.py
    - 实现 application/ports.py 中的 PaymentGateway 接口
    - MockPaymentGateway: 模拟支付处理，生成支付记录
    - 后续对接: WechatPaymentGateway / AlipayPaymentGateway
    - 调用 business_rules.py 生成: payment_id, receipt_no, transaction_id
    - SQL: INSERT INTO fee_payments (...) VALUES (...);

────────────────────────────────────────────────────────
数据库表 (6 张):
────────────────────────────────────────────────────────
  community_buildings   楼栋信息表   (PK: building_id)
  community_rooms       房号信息表   (PK: room_id, FK: building_id)
  sys_users             用户表       (PK: user_id, FK: building_id, room_id)
  fee_bills             账单主表     (PK: bill_id, FK: user_id, room_id)
  fee_payments          缴费记录表   (PK: payment_id, FK: bill_id, user_id)
  fee_receipts          电子票据表   (PK: receipt_no, FK: bill_id, user_id, payment_id)

────────────────────────────────────────────────────────
依赖方向（外层依赖内层）:
────────────────────────────────────────────────────────
  adapters (FastAPI) → application (Use Cases) → domain (Entities)
                          ↓                           ↓
                     infrastructure (Repository, LLM, Payment)
"""