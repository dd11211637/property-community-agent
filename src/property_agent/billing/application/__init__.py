"""
application/       应用层

用例编排、命令(写)、查询(读)、端口接口、数据传输对象。
组合领域层和基础设施层，实现完整业务用例。

调用链（以缴费为例）:
    HTTP POST /api/bills/pay {"bill_id": "...", "user_id": "..."}
      → adapters/api/routes.py: pay_bill()
        → use_cases.py: PaymentUseCase.pay_single(bill_id, user_id)
          → commands.py: PayBillCommand.execute(bill_id, user_id)
            → ports.py: BillRepository.find_by_id(bill_id)
              → infrastructure/repositories.py: SQLAlchemy 实现
                → SQL: SELECT * FROM fee_bills WHERE bill_id = :bill_id;
            → domain/business_rules.py: validate_payable(bill)
            → ports.py: PaymentGateway.process_payment(bill, user_id)
            → domain/state_machine.py: transition_to(bill, PAID)
            → ports.py: BillRepository.save(bill)
              → SQL: UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:id;
            → ports.py: PaymentRepository.save(payment)
              → SQL: INSERT INTO fee_payments (...) VALUES (...);
            → ports.py: ReceiptRepository.save(receipt)
              → SQL: INSERT INTO fee_receipts (...) VALUES (...);
            → ports.py: UnitOfWork.commit()
              → SQL: COMMIT;
          → dtos.py: PayResponseDTO (dataclass)
        → adapters/api/schemas.py: PayResponseSchema (Pydantic)
      → JSON Response

调用链（以查询为例）:
    HTTP GET /api/bills?user_id=user_101&role=owner
      → use_cases.py: BillQueryUseCase.list_bills(user_id, role)
        → queries.py: GetBillsByRole.execute(user_id, role)
          → ports.py: BillRepository.find_by_user(user_id)
            → SQL: SELECT * FROM fee_bills WHERE user_id = :user_id;
          → domain/state_machine.py: auto_check_overdue(bill)
          → domain/business_rules.py: calculate_late_fee(bill)
          → domain/business_rules.py: summarize_bills(bills)
        → dtos.py: BillSummaryDTO
      → 分页处理 → PaginatedBillSummarySchema

用例清单 (9 个):
  BillQueryUseCase         账单查询 (list_bills, get_bill_detail, get_user)
  PaymentUseCase           缴费 (pay_single, pay_batch, recalculate_late_fee)
  InterpretationUseCase    账单解读 (interpret, 调用 LLM)
  ReceiptUseCase           票据查询 (get_receipt)
  PaymentHistoryUseCase    支付历史 (get_user_history, get_all_history)
  ExportUseCase            账单导出 (export_csv)
  CancelBillUseCase        取消账单 (cancel, 管理员专用)
  RefundUseCase            退款 (refund, 管理员专用)
  BillGenerationUseCase    账单生成 (generate, 管理员专用)
  OverdueCheckUseCase      逾期检查 (check)

文件:
  use_cases.py      用例编排 (10 个 UseCase)
                    组合 queries + commands + ports，实现完整业务用例
                    每个方法标注等价 SQL
  commands.py       命令对象 (PayBillCommand, BatchPayCommand, CancelBillCommand,
                    RefundBillCommand, GenerateBillsCommand, CheckOverdueCommand,
                    ExportBillsCommand, UpdateLateFeeCommand)
                    写操作，每个命令标注等价 SQL 事务
  queries.py        查询对象 (GetBillsByRole, GetBillById, GetUserById,
                    GetReceiptByNo, GetPaymentHistoryByUser, GetPaymentHistoryAll)
                    只读操作，每个查询标注等价 SQL SELECT
  ports.py          端口接口 (BillRepository, UserRepository, PaymentRepository,
                    ReceiptRepository, BuildingRepository, RoomRepository,
                    UnitOfWork, LLMClient, PaymentGateway)
                    定义领域层对外部依赖的抽象接口，每个方法标注等价 SQL
  dtos.py           数据传输对象 (BillDTO, PayResponseDTO, ReceiptDTO, ...)
                    20+ DTO 类，与 adapters/api/schemas.py 一一对应
                    用于应用层与适配器层之间传递数据，不含业务逻辑
"""