"""
domain/            领域层

实体、值对象、枚举、状态机、业务规则。
所有文件独立于任何框架，纯 Python 实现。
每个类/方法均标注等价 SQL 语句。

调用链（以缴费为例）:
    HTTP POST /api/bills/pay {"bill_id": "...", "user_id": "..."}
      → adapters/api/routes.py: pay_bill()
        → application/use_cases.py: PaymentUseCase.pay_single()
          → application/commands.py: PayBillCommand.execute()
            → domain/entities.py: Bill (实体)              -- 对应 fee_bills 表
            → domain/enums.py: BillStatus (PAID)          -- CHECK 约束
            → domain/state_machine.py: transition_to()     -- 状态转换
            → domain/business_rules.py: validate_payable() -- 业务规则校验
            → domain/value_objects.py: Money (金额)        -- NUMERIC(10,2)
            → infrastructure/repositories.py: BillRepository.save()
              → SQL: UPDATE fee_bills SET status='PAID', ... WHERE bill_id=:id;

调用链（以查询为例）:
    HTTP GET /api/bills?user_id=user_101&role=owner
      → application/queries.py: GetBillsByRole.execute()
        → domain/entities.py: Bill (实体)
        → domain/state_machine.py: auto_check_overdue()   -- 自动检查逾期
        → domain/business_rules.py: calculate_late_fee()   -- 重算滞纳金
        → domain/business_rules.py: summarize_bills()      -- 汇总统计
        → domain/enums.py: BillStatus, ReminderLevel       -- 枚举判断

文件:
  entities.py        领域实体 (Building, Room, User, Bill, Payment, Receipt)
                     每个实体对应一张数据库表，标注 DDL CREATE TABLE 和 CRUD SQL
  value_objects.py   值对象 (Money, FeeDetail, BillPeriod, Address)
                     不可变，无标识符，对应数据库列类型和 CHECK 约束
  enums.py           枚举 (BillStatus, UserRole, ReminderLevel, PayMethod, ...)
                     对应数据库 CHECK 约束和字段默认值，含状态流转说明
  state_machine.py   账单状态机 (ALLOWED_TRANSITIONS, transition_to, auto_check_overdue)
                     每个状态转换对应一条 SQL UPDATE 语句
  business_rules.py  业务规则 (滞纳金计算, 分层催缴, 缴费校验, 退款校验,
                     编号生成, 账单导出CSV, 汇总统计)
                     纯函数实现，SQL 等价标注在调用方 (application/commands.py)
"""