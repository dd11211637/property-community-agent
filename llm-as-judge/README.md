# llm-as-judge — Agent 评测系统

对 `property_agent.agent` 的编排质量做双路评估：**能用代码精确判断的走规则评估器，
只能语义判断的走 LLM Judge，高风险/关键指标强制人工抽检。**

## 架构

```text
Evaluation Case ── Agent Run ──► Evaluator ──► Result
                                                          │
├─ Input              ├─ Trace          ├─ Rule-based     ├─ Per-metric Score
├─ Ground Truth       └─ Final Answer   ├─ LLM Judge      ├─ Evidence
├─ Expected Behavior                    └─ (人工抽检标记)  ├─ Failure Category
├─ Constraints                                           └─ Overall Score
└─ Rubric
```

评估路由（每条 check 独立决策）：

```text
Final Answer → 能精确匹配？   是 → 程序直评        否 → LLM Judge
Trace        → 能用规则检查？ 是 → 程序直评        否 → LLM Judge
高风险/关键指标 → 结果标记 needs_human_review，人工抽检
```

## 质量模型

```text
Agent 质量 = Task Success + Tool Correctness + Workflow Correctness
           + Final Answer Quality + Instruction Following + Safety
```

- 六个指标默认等权聚合为 Overall Score；
- **Safety 是门禁**：任一 safety check 失败 ⇒ Overall 直接归零；
- 任一 check 无法评估（LLM 不可用等）⇒ 该用例标记 `needs_human_review`；
- `high_risk: true` 的用例无论得分如何都标记人工抽检。

## 目录

```text
llm-as-judge/
├── judge/
│   ├── schemas.py          评测数据模型（用例 / 运行 / 结果）
│   ├── loader.py           用例与运行记录加载
│   ├── harness/            Agent 运行接入层
│   │   ├── base.py         AgentHarnessPort + 真实 AgentTurn → Trace 转录器
│   │   ├── recorded.py     录制回放 harness（离线评估已记录运行）
│   │   └── live.py         联机 harness（HTTP 驱动真实后端，现场运行现场转录）
│   ├── routing.py          规则 vs LLM Judge 路由决策
│   ├── rules/              规则评估器（确定性检查）
│   ├── llmjudge/           LLM Judge（DeepSeek 严格 JSON，重试一次）
│   ├── pipeline.py         用例 × 运行 → 结果 编排
│   ├── report.py           聚合 + JSON/Markdown 报告（中文，北京时间，含历史归档）
│   └── cli.py              命令行入口
├── cases/                  评测用例（JSON，按业务线，12 条）
├── runs/                   真实 Agent 运行转录（--live 模式现场生成）
├── reports/                最新报告 + history/<生成时间>/ 历次归档（不提交）
├── probe/                  探测脚本（观察真实后端行为，编写用例前用）
└── tests/                  评测系统自身测试
```

## 使用

两种评测模式：

```powershell
cd llm-as-judge

# 1. 联机模式（推荐）：现场驱动真实后端跑 Agent，转录自动落盘 runs/
#    前提：docker compose up -d --build postgres migrate seed backend
..\.venv\Scripts\python.exe -m judge run --live --cases cases --runs runs --out reports

# 2. 回放模式：评估 runs/ 下已录制的运行（不触达后端）
..\.venv\Scripts\python.exe -m judge run --cases cases --runs runs --out reports
```

联机模式说明：

- 通过公开 HTTP API 驱动（登录真实演示账号 → 发消息 → 必要时自动确认写操作），
  轨迹全部由真实服务产生，`runs/` 下不再有手工模拟数据；
- 账号映射：用例 `input.context.username` 显式指定，否则按 `role` 取默认
  （resident→zhangsan、security→security_guard、manager→manager、staff→customer_service）；
- 无 `DEEPSEEK_API_KEY` 时后端网关自动降级为确定性关键词路由（真实降级链路），
  LLM Judge 的语义项转待人工，规则项照常评估。

LLM Judge 复用 `DEEPSEEK_API_KEY` 环境变量；缺失或调用失败时相关 check
标记为待人工评审，不会静默降级为通过。

录制新的运行轨迹：

```python
from judge.harness import record_run

payload = record_run(turn, case_id="inspection_case1", agent_mode="keyword")
# payload 为可 JSON 序列化 dict，写入 runs/<case_id>.json
```

## 约束

- 本目录是离线评测工具，不进入 `src/` 生产代码，生产代码不导入 `judge`。
- `judge` 可以 import `property_agent`（转录真实运行），反向禁止。
- 规则评估器必须确定性：同输入同输出，不依赖网络与随机性。
- LLM Judge 只输出 `score / evidence / failure_category / reasoning`，
  不产出新的业务事实。
