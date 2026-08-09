"""
billing/            费用查询与智能缴费模块

DDD 四层架构，完整调用链:

    HTTP 请求 / AI Agent 调用
      → adapters/          适配器层
        ├── api/routes.py       FastAPI 路由 (12 个端点)
        │   每个端点标注完整的调用链和等价 SQL 语句
        │   ├── GET    /api/bills                    账单列表（支持分页: ?page=1&page_size=20）
        │   ├── GET    /api/bills/detail/{bill_id}   账单详情
        │   ├── GET    /api/bills/export             账单导出（CSV 下载）
        │   ├── POST   /api/bills/interpret          AI 账单解读
        │   ├── POST   /api/bills/pay                单笔缴费
        │   ├── POST   /api/bills/pay/batch          批量缴费
        │   ├── POST   /api/bills/refund             退款（管理员）
        │   ├── POST   /api/bills/cancel             取消账单（管理员）
        │   ├── POST   /api/bills/generate           批量生成账单（管理员）
        │   ├── POST   /api/bills/schedule-overdue   逾期自动检查
        │   ├── GET    /api/bills/payments/history   支付历史
        │   └── GET    /api/bills/receipt/{no}       电子票据查询
        ├── api/schemas.py      Pydantic 请求/响应模型 (20+ Schema)
        │   与 application/dtos.py 中的 DTO 一一对应
        └── agent_tools.py      AI Agent 工具集 (BillingAgentTools)
            供 agent/ 层的智能体编排使用，每个工具方法封装完整的业务逻辑
        → application/      应用层
          ├── use_cases.py      用例编排 (BillQueryUseCase, PaymentUseCase, ...)
          │   组合 queries + commands + ports，实现完整业务用例
          ├── commands.py       命令对象 (PayBillCommand, RefundBillCommand, ...)
          │   写操作，每个命令标注等价 SQL 事务
          ├── queries.py        查询对象 (GetBillsByRole, GetBillById, ...)
          │   只读操作，每个查询标注等价 SQL SELECT
          ├── ports.py          端口接口 (BillRepository, LLMClient, UnitOfWork, ...)
          │   定义领域层对外部依赖的抽象接口
          └── dtos.py           数据传输对象 (BillDTO, PayResponseDTO, ...)
              用于应用层与适配器层之间传递数据，不含业务逻辑
          → domain/          领域层
            ├── entities.py        实体 (Building, Room, User, Bill, Payment, Receipt)
            │   每个实体对应一张数据库表，标注 DDL 和 CRUD SQL
            ├── value_objects.py   值对象 (Money, FeeDetail, BillPeriod, Address)
            │   不可变，对应数据库列类型
            ├── enums.py           枚举 (BillStatus, UserRole, ReminderLevel, ...)
            │   对应数据库 CHECK 约束
            ├── state_machine.py   账单状态机 (ALLOWED_TRANSITIONS, transition_to)
            │   每个转换对应一条 SQL UPDATE
            └── business_rules.py  业务规则 (滞纳金计算, 催缴, 校验, 编号生成)
                纯函数，SQL 标注在调用方
            → infrastructure/  基础设施层
              ├── database.py        数据库连接 (SQLAlchemy, support SQLite/PostgreSQL)
              │   SQL: CREATE TABLE IF NOT EXISTS, SELECT, INSERT, UPDATE, DELETE
              ├── orm_models.py      ORM 模型 (BuildingModel, BillModel, ...)
              │   6 张表，完整的 DDL + 索引 + 外键 + CHECK 约束
              ├── repositories.py    仓储实现 (SqlAlchemyBillRepository, ...)
              │   实现 ports.py 接口，每个方法标注等价 SQL
              ├── llm_client.py      LLM 客户端 (HttpLLMClient)
              │   支持 OpenAI/Qwen/DeepSeek，无 Key 降级内置模板
              └── payment_gateway.py 支付网关 (MockPaymentGateway)
                  模拟支付，后续对接微信/支付宝

核心数据表 (6 张):
  community_buildings  楼栋信息表
  community_rooms      房号信息表
  sys_users            用户表
  fee_bills            账单主表
  fee_payments         缴费记录表
  fee_receipts         电子票据表

业务闭环 (5 步):
  账单查询 → AI解读 → 分层催缴 → 一键缴费 → 电子票据

扩展功能:
  退款 | 取消账单 | 批量缴费 | 账单导出(CSV) | 支付历史 | 账单自动生成 | 逾期自动检查 | 分页

权限控制 (3 角色):
  owner (业主)    → 查询本人账单、缴费、解读
  staff (物业)    → 查询负责楼栋的账单
  admin (管理员)  → 全社区账单、退款、作废、生成账单、逾期检查
"""