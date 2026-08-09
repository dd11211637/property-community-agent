"""
infrastructure/llm_client.py     LLM 客户端实现

实现 application/ports.py 中的 LLMClient 接口。
支持 OpenAI / Qwen / DeepSeek，无 API Key 时降级内置模板。

数据流:
    1. 查询账单: SELECT * FROM fee_bills WHERE bill_id = :bill_id;
    2. 查询用户: SELECT user_name FROM sys_users WHERE user_id = :user_id;
    3. 构建 Prompt → 调用 LLM API → 返回解读文本
    4. 降级方案: 内置模板生成解读
"""
from __future__ import annotations
import os
import httpx

from ..application.ports import LLMClient
from ..domain.entities import Bill
from ..domain.enums import ReminderLevel
from ..domain.business_rules import determine_reminder_level, generate_reminder_text

# ── Prompt 模板 ──────────────────────────────────────

BILL_INTERPRET_SYSTEM_PROMPT = (
    "你是一名贴心的社区 AI 物业管家，名叫「小智」。"
    "请将给定的账单 JSON 数据转化为口语化、亲切且易懂的语言向业主解读。"
    "需详细说明每一笔费用构成（特别是公摊水电和滞纳金的原因），"
    "并提示业主如何快捷缴费。"
    "语气要温暖、耐心，像朋友在聊天一样。"
    "回复控制在 200 字以内，用第二人称「您」称呼业主。"
)


def _build_user_prompt(bill: Bill, user_name: str) -> str:
    """
    构建发送给 LLM 的用户提示词。

    数据来源 SQL:
        SELECT f.bill_period, f.property_fee, f.utility_fee, f.parking_fee,
               f.late_fee, f.total_amount, f.due_date, f.status,
               u.user_name
          FROM fee_bills f
          JOIN sys_users u ON f.user_id = u.user_id
         WHERE f.bill_id = :bill_id;
    """
    status_map = {
        "UNPAID": "未到期（还不需要缴费，但请留意最迟缴费日）",
        "OVERDUE": "已逾期（已超过最迟缴费日，产生了滞纳金）",
        "PAID": "已缴费",
    }
    return f"""
请帮 {user_name} 业主解读以下账单：

- 账期：{bill.bill_period}
- 物业费：{bill.property_fee} 元
- 公摊水电费：{bill.utility_fee} 元
- 车位费：{bill.parking_fee} 元
- 滞纳金：{bill.late_fee} 元
- 合计：{bill.total_amount} 元
- 最迟缴费日：{bill.due_date}
- 状态：{status_map.get(bill.status.value if hasattr(bill.status, 'value') else bill.status, bill.status)}

请用亲切的口语化语言解读。
"""


class HttpLLMClient(LLMClient):
    """
    基于 HTTP 的 LLM 客户端实现。

    自动检测环境变量中的 API Key:
        OPENAI_API_KEY → OpenAI (gpt-3.5-turbo)
        QWEN_API_KEY   → 通义千问 (qwen-plus)
        DEEPSEEK_API_KEY → DeepSeek (deepseek-chat)

    无 API Key 时降级为内置模板。

    调用链:
        InterpretationUseCase.interpret()
          → HttpLLMClient.interpret_bill()
            → 有 Key: POST {provider}/v1/chat/completions
            → 无 Key: _fallback_interpretation()
          → 返回 (解读文本, 提醒层级, 提醒文案)
    """

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

    def _detect_provider(self) -> tuple[str, str, str]:
        """检测可用的 LLM 提供商"""
        for provider, config in self.API_CONFIGS.items():
            key = os.environ.get(config["key_env"])
            if key:
                return provider, key, config["model"]
        return "", "", ""

    async def interpret_bill(self, bill: Bill, user_name: str) -> tuple[str, ReminderLevel, str]:
        """
        调用 LLM 解读账单。

        前置 SQL:
            SELECT * FROM fee_bills WHERE bill_id = :bill_id;
            SELECT user_name FROM sys_users WHERE user_id = :user_id;

        返回 (解读文本, 提醒层级, 提醒文案)
        """
        # 计算催缴层级
        rem_level = determine_reminder_level(bill)
        rem_text = generate_reminder_text(bill, rem_level)

        provider, api_key, model = self._detect_provider()

        if not api_key:
            # 降级: 使用内置模板
            interpretation = self._fallback_interpretation(bill, user_name)
            return interpretation, rem_level, rem_text

        try:
            config = self.API_CONFIGS[provider]
            user_prompt = _build_user_prompt(bill, user_name)

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
                self._fallback_interpretation(bill, user_name)
                + f"\n\n（小智提示：AI 解读服务暂时不可用，以上为系统自动生成解读。错误：{e}）"
            )

        return interpretation, rem_level, rem_text

    def _fallback_interpretation(self, bill: Bill, user_name: str) -> str:
        """
        内置模板解读（无 API Key 时的降级方案）。

        数据来源 SQL:
            SELECT f.bill_period, f.property_fee, f.utility_fee, f.parking_fee,
                   f.late_fee, f.total_amount, f.due_date, f.status
              FROM fee_bills f
             WHERE f.bill_id = :bill_id;
        """
        status_map = {"UNPAID": "还未到期", "OVERDUE": "已经逾期了", "PAID": "已缴费完成"}
        status_text = bill.status.value if hasattr(bill.status, 'value') else bill.status
        s = status_map.get(status_text, "")

        items = []
        if bill.property_fee:
            items.append(f"物业费 {bill.property_fee:.2f} 元")
        if bill.utility_fee:
            items.append(f"公摊水电费 {bill.utility_fee:.2f} 元")
        if bill.parking_fee:
            items.append(f"车位费 {bill.parking_fee:.2f} 元")
        if bill.late_fee:
            items.append(f"滞纳金 {bill.late_fee:.2f} 元")
        detail = "、".join(items)

        return (
            f"{user_name}您好！我是您的社区管家小智～\n\n"
            f"您 {bill.bill_period} 的账单已经生成，共 {bill.total_amount:.2f} 元，"
            f"包含：{detail}。\n\n"
            f"这笔账单目前{s}，最迟缴费日为 {bill.due_date}。"
            f"您可以在页面下方点击「一键缴费」按钮完成支付，"
            f"缴费后会自动生成电子票据，方便您随时查看和下载～"
        )