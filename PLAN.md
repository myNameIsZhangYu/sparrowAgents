# Super Agent 升级计划

> 本计划按可交付增量执行。每完成一个任务后实时更新状态与证据，未完成项必须保留待办。

## 状态

| 任务 | 状态 | 交付物 | 证据 |
|---|---|---|---|
| 1. 计划模型与并行执行增强 | 已完成 | `PLAN.md`, `tests/test_plan_model.py`, DAG 集成 | `tests/test_plan_model.py`: 4 passed；定向多 Agent 测试: 32 passed, 5 skipped |
| 2. MCP 客户端与动态工具治理 | 已完成 | MCP 客户端、健康检查、治理测试 | `tests/test_mcp_http.py`: 13 passed；`tests/test_mcp_runtime.py`: 5 passed |
| 3. Agent Runtime 能力模型 | 已完成 | 独立能力/权限/并发模型与测试 | `tests/test_agent_runtime.py`: 6 passed；全量: 161 passed, 9 skipped |
| 4. 长任务 checkpoint 与 artifacts | 已完成 | SQLite/Postgres checkpoint 与 artifact 接口 | `tests/test_task_checkpoints.py`: 2 passed；定向持久化/恢复: 19 passed |
| 5. 评估指标扩展 | 已完成 | 综合 Super Agent 评估指标 | `tests/test_super_agent_metrics.py`: 5 passed |
| 6. 技能库最小闭环 | 已完成 | 技能 Schema、版本化、检索 | `tests/test_skill_store.py`: 3 passed |
| 7. 文档与发布门禁 | 已完成 | 架构/开发/运维文档与验收清单 | 全量测试: 168 passed, 9 skipped；ruff: 通过；multi-agent eval: passed |

## 里程碑

### 1. 计划模型与并行执行增强

- [x] **1.1** 定义 `PlanModel` / `PlanStep`：目标、里程碑、依赖 DAG、预算、风险、超时。
- [x] **1.2** 兼容旧 planner JSON，并对模型非法输出降级为旧计划。
- [x] **1.3** 将新模型接入 LangGraph executor，保持确认、失败、critic 行为不变。
- [x] **1.4** 增加 DAG、预算、风险、并行执行测试。
- [x] **1.5** 运行定向测试与全量测试，更新本文件状态。

### 2. MCP 客户端与动态工具治理

- [x] **2.1** 定义 HTTP/JSON MCP 适配协议：`tools/list`、`tools/call`、健康检查。
- [x] **2.2** 实现 MCP Tool Registry 同步与 ToolDef 转换。
- [x] **2.3** 将 MCP 工具接入权限策略、确认工作流、审计和超时。
- [x] **2.4** 增加禁网单测与真实 MCP 开关测试。
- [x] **2.5** 运行测试并更新状态。

### 3. Agent Runtime 能力模型

- [x] **3.1** 定义 Agent Runtime 规范：persona、技能、工具权限、记忆、并发、状态。
- [x] **3.2** 将能力注册表扩展为可序列化配置，并支持按任务选择 Agent。
- [x] **3.3** 增加跨子任务上下文隔离与消息传递测试。
- [x] **3.4** 运行测试并更新状态。

### 4. 长任务 checkpoint 与 artifacts

- [x] **4.1** 定义子任务 checkpoint schema 和 artifact 引用。
- [x] **4.2** SQLite/Postgres store 增加 artifact 记录接口。
- [x] **4.3** 支持失败子任务从 checkpoint 恢复。
- [x] **4.4** 增加持久化与恢复测试。
- [x] **4.5** 运行测试并更新状态。

### 5. 评估指标扩展

- [x] **5.1** 多 Agent 评估纳入 DAG 通过率、并行度、重试率。
- [x] **5.2** 纳入 token、成本、延迟、安全事件、恢复成功率。
- [x] **5.3** 增加聚合评分与质量门。
- [x] **5.4** 运行评估测试并更新状态。

### 6. 技能库最小闭环

- [x] **6.1** 定义技能 JSON Schema：输入、计划片段、工具、约束、成功证据。
- [x] **6.2** 实现技能存储、检索与版本记录。
- [x] **6.3** 支持成功任务沉淀技能与灰度使用。
- [x] **6.4** 增加测试并更新状态。

### 7. 文档与发布门禁

- [x] **7.1** 更新架构、运维、安全和评估文档。
- [x] **7.2** 建立全量验收清单。
- [x] **7.3** 运行全量测试、ruff 和关键 eval。
- [x] **7.4** 输出最终状态与后续建议。
