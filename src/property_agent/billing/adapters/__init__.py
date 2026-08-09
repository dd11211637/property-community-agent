"""
adapters/          适配器层

将 application 层用例暴露为外部可调用的接口。
包含 HTTP API 路由、请求/响应 Schema、AI Agent 工具封装。

────────────────────────────────────────────────────────
调用链（完整请求链路）:
────────────────────────────────────────────────────────
    ┌─ HTTP 请求 ─────────────────────────────────────┐
    │                                                  │
    │  adapters/api/routes.py       FastAPI 路由       │
    │    ↓                                             │
    │  adapters/api/schemas.py      Pydantic 校验      │
    │    ↓                                             │
    │  application/use_cases.py     用例编排           │
    │    ├── application/commands.py   写命令          │
    │    ├── application/queries.py    读查询          │
    │    └── application/ports.py      端口接口        │
    │      ↓                                           │
    │  infrastructure/               基础设施实现      │
    │    ├── repositories.py     SQLAlchemy 仓储       │
    │    ├── llm_client.py       LLM API 调用          │
    │    └── payment_gateway.py  支付网关模拟          │
    │      ↓                                           │
    │  SQL: SELECT/INSERT/UPDATE/DELETE ...            │
    │                                                  │
    └──────────────────────────────────────────────────┘

    ┌─ AI Agent 调用 ────────────────────────────────┐
    │                                                  │
    │  adapters/agent_tools.py      BillingAgentTools  │
    │    ↓                                             │
    │  application/use_cases.py     用例编排           │
    │    ↓                          (同上)             │
    │  infrastructure/              基础设施实现       │
    │                                                  │
    └──────────────────────────────────────────────────┘

────────────────────────────────────────────────────────
API 端点 (12 个):
────────────────────────────────────────────────────────
  GET    /api/bills                    账单列表（支持分页: ?page=1&page_size=20）
  GET    /api/bills/detail/{bill_id}   账单详情
  GET    /api/bills/export             账单导出（CSV 下载）
  POST   /api/bills/interpret          AI 账单解读
  POST   /api/bills/pay                单笔缴费
  POST   /api/bills/pay/batch          批量缴费
  POST   /api/bills/refund             退款（管理员）
  POST   /api/bills/cancel             取消账单（管理员）
  POST   /api/bills/generate           批量生成账单（管理员）
  POST   /api/bills/schedule-overdue   逾期自动检查
  GET    /api/bills/payments/history   支付历史
  GET    /api/bills/receipt/{no}       电子票据查询

────────────────────────────────────────────────────────
Agent 工具 (BillingAgentTools, 12 个方法):
────────────────────────────────────────────────────────
  query_bills()         查询账单
  interpret_bill()      AI 解读账单
  pay_bill()            单笔缴费
  pay_batch()           批量缴费
  get_user()            查询用户
  get_receipt()         查询电子票据
  get_payment_history() 查询支付历史
  export_bills_csv()    导出账单 CSV
  cancel_bill()         取消账单
  refund_bill()         退款
  generate_bills()      批量生成账单
  check_overdue()       逾期自动检查

────────────────────────────────────────────────────────
文件:
────────────────────────────────────────────────────────
  agent_tools.py
    - AI Agent 工具集 (BillingAgentTools)
    - 供 agent/ 层的智能体编排使用
    - 每个工具方法封装完整的业务逻辑: 查询 → 校验 → 执行 → 返回
    - 内部自动完成数据库查询、业务规则校验、结果返回

  api/
    routes.py
      - FastAPI 路由 (12 个端点)
      - 每个端点标注完整的调用链和等价 SQL 语句
      - 依赖注入工厂函数 (10 个 _*_uc 函数)
      - 权限控制: owner 查本人, staff 查楼栋, admin 查全部

    schemas.py
      - Pydantic 请求/响应模型 (20+ Schema)
      - 与 application/dtos.py 中的 DTO 一一对应
      - 每个 Schema 标注对应的 SQL 查询语句
      - 请求: InterpretRequest, PayRequest, BatchPayRequest, CancelRequest,
              RefundRequest, GenerateBillsRequest
      - 响应: BillSchema, PaginatedBillSummarySchema, InterpretResponseSchema,
              PayResponseSchema, BatchPayResponseSchema, ReceiptSchema,
              CancelResponseSchema, RefundResponseSchema,
              GenerateBillsResponseSchema, OverdueCheckResponseSchema,
              PaymentHistoryItemSchema, PaymentHistoryResponseSchema, UserSchema
"""