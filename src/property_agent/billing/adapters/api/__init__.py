"""
adapters/api/      FastAPI 路由与 Schema

将 application 层所有用例暴露为 RESTful HTTP API。
每个路由端点标注完整的调用链和等价 SQL 语句。

────────────────────────────────────────────────────────
路由端点 (12 个):
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
调用链（以账单查询为例）:
────────────────────────────────────────────────────────
  HTTP GET /api/bills?user_id=user_101&role=owner&page=1&page_size=20
    → routes.list_bills()
      → _bill_query_uc() → BillQueryUseCase
        → BillQueryUseCase.list_bills(user_id, role)
          → GetBillsByRole.execute(user_id, role)
            → SqlAlchemyBillRepository.find_by_user(user_id)
              → SQL: SELECT f.*, u.user_name, r.room_number, b.building_name
                     FROM fee_bills f
                     JOIN sys_users u ON f.user_id = u.user_id
                     JOIN community_rooms r ON f.room_id = r.room_id
                     JOIN community_buildings b ON r.building_id = b.building_id
                     WHERE f.user_id = :user_id
                     ORDER BY f.bill_period DESC;
            → auto_check_overdue(bill) + calculate_late_fee(bill)
            → summarize_bills(bills)
          → BillSummaryDTO → PaginatedBillSummarySchema

────────────────────────────────────────────────────────
调用链（以缴费为例）:
────────────────────────────────────────────────────────
  HTTP POST /api/bills/pay {"bill_id": "bill_202607_101", "user_id": "user_101"}
    → routes.pay_bill()
      → _payment_uc() → PaymentUseCase
        → PaymentUseCase.pay_single(bill_id, user_id)
          → PayBillCommand.execute(bill_id, user_id)
            → SQL: SELECT * FROM fee_bills WHERE bill_id = :bill_id;
            → validate_payable(bill)  -- 业务规则校验
            → MockPaymentGateway.process_payment(bill, user_id)  -- 模拟支付
            → transition_to(bill, PAID)  -- 状态转换
            → SQL: BEGIN;
            → SQL: UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:id;
            → SQL: INSERT INTO fee_payments (...) VALUES (...);
            → SQL: INSERT INTO fee_receipts (...) VALUES (...);
            → SQL: COMMIT;
          → PayResponseDTO → PayResponseSchema

────────────────────────────────────────────────────────
依赖注入工厂 (10 个):
────────────────────────────────────────────────────────
  _bill_query_uc()     → BillQueryUseCase       (账单查询)
  _payment_uc()        → PaymentUseCase         (缴费)
  _interpretation_uc() → InterpretationUseCase  (AI 解读)
  _receipt_uc()        → ReceiptUseCase         (票据查询)
  _history_uc()        → PaymentHistoryUseCase  (支付历史)
  _export_uc()         → ExportUseCase          (账单导出)
  _cancel_uc()         → CancelBillUseCase      (取消账单)
  _refund_uc()         → RefundUseCase          (退款)
  _generation_uc()     → BillGenerationUseCase  (账单生成)
  _overdue_uc()        → OverdueCheckUseCase    (逾期检查)

────────────────────────────────────────────────────────
文件:
────────────────────────────────────────────────────────
  routes.py
    - FastAPI APIRouter (prefix="/api/bills", tags=["费用管理"])
    - 12 个路由端点，每个端点标注完整的调用链和等价 SQL 语句
    - 10 个依赖注入工厂函数，为每个 UseCase 组装所有依赖
    - 权限控制: owner 查本人, staff 查楼栋, admin 查全部

  schemas.py
    - Pydantic BaseModel (20+ Schema)
    - 与 application/dtos.py 中的 DTO 一一对应
    - 每个 Schema 标注对应 SQL 查询/事务语句
    - 请求模型 (6 个): InterpretRequest, PayRequest, BatchPayRequest,
                       CancelRequest, RefundRequest, GenerateBillsRequest
    - 响应模型 (13 个): BillSchema, BillSummarySchema, PaginatedBillSummarySchema,
                        FeeItemSchema, InterpretResponseSchema, PayResponseSchema,
                        BatchPayResponseSchema, CancelResponseSchema,
                        RefundResponseSchema, GenerateBillsResponseSchema,
                        OverdueCheckResponseSchema, ReceiptSchema, UserSchema,
                        PaymentHistoryItemSchema, PaymentHistoryResponseSchema
"""