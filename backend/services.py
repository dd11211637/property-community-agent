"""
业务服务层 - 物业社区管理AI智能体 · 费用查询与智能缴费模块
基于 SQLAlchemy ORM 的真实数据库查询，替代 Mock 数据版本。
"""
from __future__ import annotations
import os
import httpx
from datetime import datetime, date
from decimal import Decimal
from typing import Optional
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session, joinedload

from models_db import Building, Room, User, Bill, Payment, Receipt
from prompts import (
    BILL_INTERPRET_SYSTEM_PROMPT,
    build_interpret_user_prompt,
    get_reminder,
)

# ── 当前日期 ──────────────────────────────────────────
TODAY = date(2026, 7, 28)


# ── 账单服务 ──────────────────────────────────────────

class BillService:
    """账单查询、缴费服务 — 真实数据库查询"""

    @staticmethod
    def get_bills(db: Session, user_id: str, role: str) -> dict:
        """
        获取账单列表及欠费汇总。

        角色权限:
          - owner: 仅查询本人账单
          - staff: 查询其负责楼栋的所有业主账单
          - admin: 查询全社区所有账单

        Returns:
          { total_unpaid, unpaid_count, bills: [...] }
        """
        query = db.query(Bill).options(
            joinedload(Bill.user_ref),
            joinedload(Bill.room_ref),
        )

        if role == "owner":
            # 业主: 只看自己的账单
            query = query.filter(Bill.user_id == user_id)
        elif role == "staff":
            # 物业员工: 看自己负责楼栋的账单
            staff = db.query(User).filter(User.user_id == user_id).first()
            if staff and staff.building_id:
                room_ids = db.query(Room.room_id).filter(
                    Room.building_id == staff.building_id
                ).subquery()
                query = query.filter(Bill.room_id.in_(room_ids))
            # 若无楼栋归属，则返回空
        # admin: 无过滤，返回全部

        bills = query.order_by(Bill.bill_period.desc()).all()

        # 汇总欠费
        unpaid_bills = [b for b in bills if b.status in ("UNPAID", "OVERDUE")]
        total_unpaid = sum(float(b.total_amount) for b in unpaid_bills)

        return {
            "total_unpaid": round(total_unpaid, 2),
            "unpaid_count": len(unpaid_bills),
            "bills": [_bill_to_dict(b) for b in bills],
        }

    @staticmethod
    def get_bill_by_id(db: Session, bill_id: str) -> Optional[Bill]:
        """按 ID 获取账单"""
        return db.query(Bill).options(
            joinedload(Bill.user_ref),
            joinedload(Bill.room_ref),
        ).filter(Bill.bill_id == bill_id).first()

    @staticmethod
    def pay_bill(db: Session, bill_id: str, user_id: str) -> dict:
        """
        模拟支付流程:
          1. 校验账单状态
          2. 更新账单为 PAID
          3. 创建缴费记录
          4. 生成电子票据
          5. 返回支付结果
        """
        bill = db.query(Bill).filter(Bill.bill_id == bill_id).first()
        if not bill:
            raise ValueError(f"账单 {bill_id} 不存在")
        if bill.status == "PAID":
            raise ValueError(f"账单 {bill_id} 已经缴费过了")

        now = datetime.now()
        today_str = now.strftime("%Y%m%d")
        user_suffix = user_id.split("_")[-1]

        # 生成单据号
        last_payment = db.query(Payment).order_by(Payment.payment_id.desc()).first()
        pay_seq = int(last_payment.payment_id.split("_")[-1]) + 1 if last_payment else 3
        payment_id = f"pay_{pay_seq:03d}"
        receipt_no = f"REC_{today_str}_{user_suffix}"
        transaction_id = f"TXN_{today_str}_{pay_seq:03d}"

        # 1) 更新账单状态
        bill.status = "PAID"
        bill.payment_time = now
        bill.receipt_no = receipt_no

        # 2) 创建缴费记录
        payment = Payment(
            payment_id=payment_id,
            bill_id=bill_id,
            user_id=user_id,
            pay_amount=bill.total_amount,
            pay_method="WECHAT",
            pay_status="SUCCESS",
            transaction_id=transaction_id,
            receipt_no=receipt_no,
            paid_at=now,
        )
        db.add(payment)

        # 3) 创建电子票据
        receipt = Receipt(
            receipt_no=receipt_no,
            bill_id=bill_id,
            user_id=user_id,
            payment_id=payment_id,
            period=bill.bill_period,
            property_fee=bill.property_fee,
            utility_fee=bill.utility_fee,
            parking_fee=bill.parking_fee,
            late_fee=bill.late_fee,
            total_amount=bill.total_amount,
            issue_time=now,
        )
        db.add(receipt)

        db.commit()
        db.refresh(bill)

        return {
            "success": True,
            "message": "缴费成功！您的电子票据已生成。",
            "bill_id": bill_id,
            "receipt_no": receipt_no,
            "paid_amount": float(bill.total_amount),
            "payment_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        }


# ── LLM 服务 ──────────────────────────────────────────

class LLMService:
    """大模型解读服务，支持 OpenAI / Qwen / DeepSeek"""

    API_CONFIGS = {
        "openai": {
            "url": "https://api.openai.com/v1/chat/completions",
            "key_env": "OPENAI_API_KEY",
            "model": "gpt-3.5-turbo",
        },
        "qwen": {
            "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "key_env": "QWEN_API_KEY",
            "model": "qwen-plus",
        },
        "deepseek": {
            "url": "https://api.deepseek.com/v1/chat/completions",
            "key_env": "DEEPSEEK_API_KEY",
            "model": "deepseek-chat",
        },
    }

    @classmethod
    def _detect_provider(cls) -> tuple[str, str, str]:
        for provider, config in cls.API_CONFIGS.items():
            key = os.environ.get(config["key_env"])
            if key:
                return provider, key, config["model"]
        return "", "", ""

    @classmethod
    async def interpret_bill(cls, bill: Bill, user_name: str) -> dict:
        """调用大模型 API 解读账单，无 API Key 时降级内置模板"""
        provider, api_key, model = cls._detect_provider()
        reminder_level, reminder_text = get_reminder(bill, TODAY)

        if not api_key:
            interpretation = cls._fallback_interpretation(bill, user_name)
        else:
            try:
                config = cls.API_CONFIGS[provider]
                user_prompt = build_interpret_user_prompt(bill, user_name)
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        config["url"],
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": [
                                {"role": "system", "content": BILL_INTERPRET_SYSTEM_PROMPT},
                                {"role": "user", "content": user_prompt},
                            ],
                            "temperature": 0.7,
                            "max_tokens": 400,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    interpretation = data["choices"][0]["message"]["content"]
            except Exception as e:
                interpretation = (
                    cls._fallback_interpretation(bill, user_name)
                    + f"\n\n（小智提示：AI 解读服务暂时不可用，以上为系统自动生成解读。错误：{e}）"
                )

        return {
            "bill_id": bill.bill_id,
            "interpretation": interpretation,
            "reminder_level": reminder_level,
            "reminder_text": reminder_text,
        }

    @staticmethod
    def _fallback_interpretation(bill: Bill, user_name: str) -> str:
        status_text = {"UNPAID": "还未到期", "OVERDUE": "已经逾期了", "PAID": "已缴费完成"}
        s = status_text.get(bill.status, "")

        items = []
        if float(bill.property_fee):
            items.append(f"物业费 {float(bill.property_fee):.2f} 元")
        if float(bill.utility_fee):
            items.append(f"公摊水电费 {float(bill.utility_fee):.2f} 元")
        if float(bill.parking_fee):
            items.append(f"车位费 {float(bill.parking_fee):.2f} 元")
        if float(bill.late_fee):
            items.append(f"滞纳金 {float(bill.late_fee):.2f} 元")
        detail = "、".join(items)

        return (
            f"{user_name}您好！我是您的社区管家小智～\n\n"
            f"您 {bill.bill_period} 的账单已经生成，共 {float(bill.total_amount):.2f} 元，"
            f"包含：{detail}。\n\n"
            f"这笔账单目前{s}，最迟缴费日为 {bill.due_date}。"
            f"您可以在页面下方点击「一键缴费」按钮完成支付，"
            f"缴费后会自动生成电子票据，方便您随时查看和下载～"
        )


# ── 票据服务 ──────────────────────────────────────────

class ReceiptService:
    """电子票据查询服务"""

    @staticmethod
    def get_receipt(db: Session, receipt_no: str) -> Optional[dict]:
        """获取票据详情"""
        receipt = db.query(Receipt).options(
            joinedload(Receipt.user_ref),
            joinedload(Receipt.bill_ref),
        ).filter(Receipt.receipt_no == receipt_no).first()

        if not receipt:
            return None

        user = receipt.user_ref
        return {
            "receipt_no": receipt.receipt_no,
            "bill_id": receipt.bill_id,
            "user_id": receipt.user_id,
            "user_name": user.user_name if user else "",
            "building": user.building_ref.building_name if user and user.building_ref else "",
            "room": user.room_ref.room_number if user and user.room_ref else "",
            "period": receipt.period,
            "items": {
                "property_fee": float(receipt.property_fee),
                "utility_fee": float(receipt.utility_fee),
                "parking_fee": float(receipt.parking_fee),
                "late_fee": float(receipt.late_fee),
            },
            "total_amount": float(receipt.total_amount),
            "payment_time": receipt.issue_time.strftime("%Y-%m-%d %H:%M:%S") if receipt.issue_time else "",
            "issue_time": receipt.issue_time.strftime("%Y-%m-%d %H:%M:%S") if receipt.issue_time else "",
            "note": "电子票据，与纸质票据具有同等效力",
        }


# ── 用户服务 ──────────────────────────────────────────

class UserService:
    """用户查询服务"""

    @staticmethod
    def get_user(db: Session, user_id: str) -> Optional[dict]:
        """获取用户信息"""
        user = db.query(User).options(
            joinedload(User.building_ref),
            joinedload(User.room_ref),
        ).filter(User.user_id == user_id).first()

        if not user:
            return None

        return {
            "user_id": user.user_id,
            "name": user.user_name,
            "role": user.role,
            "building": user.building_ref.building_name if user.building_ref else "",
            "room": user.room_ref.room_number if user.room_ref else "",
            "phone": user.phone or "",
        }


# ── 辅助函数 ──────────────────────────────────────────

def _bill_to_dict(bill: Bill) -> dict:
    """将 ORM Bill 对象转为字典"""
    return {
        "bill_id": bill.bill_id,
        "user_id": bill.user_id,
        "period": bill.bill_period,
        "property_fee": float(bill.property_fee),
        "utility_fee": float(bill.utility_fee),
        "parking_fee": float(bill.parking_fee),
        "late_fee": float(bill.late_fee),
        "total_amount": float(bill.total_amount),
        "due_date": bill.due_date.isoformat() if bill.due_date else "",
        "status": bill.status,
        "payment_time": bill.payment_time.strftime("%Y-%m-%d %H:%M:%S") if bill.payment_time else None,
        "receipt_no": bill.receipt_no,
    }