"""
infrastructure/payment_gateway.py     支付网关实现

实现 application/ports.py 中的 PaymentGateway 接口。
当前为模拟实现，后续对接真实支付网关（微信支付/支付宝）。

────────────────────────────────────────────────────────
调用链（以缴费为例）:
────────────────────────────────────────────────────────
  PaymentUseCase.pay_single(bill_id, user_id)
    → PayBillCommand.execute(bill_id, user_id)
      → PaymentGateway.process_payment(bill, user_id)
        → MockPaymentGateway.process_payment(bill, user_id)
          → generate_payment_id(None)          -- 生成支付ID
          → generate_receipt_no(user_id, now)  -- 生成票据号
          → generate_transaction_id(pid, now)  -- 生成流水号
          → 返回 Payment(payment_id, bill_id, user_id, ...)
        → SQL: INSERT INTO fee_payments (...) VALUES (...);

────────────────────────────────────────────────────────
后续对接真实支付网关:
────────────────────────────────────────────────────────
  MockPaymentGateway       当前模拟实现
  → WechatPaymentGateway   微信支付 (JSAPI/NATIVE/H5)
  → AlipayPaymentGateway   支付宝 (当面付/手机网站/APP支付)
"""
from __future__ import annotations
from datetime import datetime

from ..application.ports import PaymentGateway
from ..domain.entities import Bill, Payment
from ..domain.enums import PayMethod, PayStatus
from ..domain.business_rules import (
    generate_payment_id, generate_receipt_no, generate_transaction_id,
)


# ═══════════════════════════════════════════════════════════════
# MockPaymentGateway · 模拟支付网关
# ═══════════════════════════════════════════════════════════════

class MockPaymentGateway(PaymentGateway):
    """
    模拟支付网关实现。

    后续对接真实支付网关时，替换为:
        WechatPaymentGateway  (微信支付)
        AlipayPaymentGateway  (支付宝)

    支付流程:
        1. 生成支付ID:     pay_001
        2. 生成票据号:     REC_20260729_101
        3. 生成流水号:     TXN_20260729_001
        4. 返回 Payment 实体（状态: SUCCESS）
    """

    def process_payment(self, bill: Bill, user_id: str, method: str = "WECHAT") -> Payment:
        """
        模拟支付处理。

        参数:
            bill:     账单实体（含 total_amount）
            user_id:  缴费用户ID
            method:   支付方式（WECHAT/ALIPAY/BANK_CARD/CASH/OFFLINE）

        返回:
            Payment 实体（支付成功）

        SQL:
            INSERT INTO fee_payments
                (payment_id, bill_id, user_id, pay_amount, pay_method, pay_status,
                 transaction_id, receipt_no, paid_at, created_at)
            VALUES
                (:payment_id, :bill_id, :user_id, :amount, :method, 'SUCCESS',
                 :txn_id, :receipt_no, :paid_at, NOW());
        """
        now = datetime.now()
        payment_id = generate_payment_id(None)
        receipt_no = generate_receipt_no(user_id, now)
        transaction_id = generate_transaction_id(payment_id, now)

        return Payment(
            payment_id=payment_id,
            bill_id=bill.bill_id,
            user_id=user_id,
            pay_amount=bill.total_amount,
            pay_method=PayMethod(method),
            pay_status=PayStatus.SUCCESS,
            transaction_id=transaction_id,
            receipt_no=receipt_no,
            paid_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )