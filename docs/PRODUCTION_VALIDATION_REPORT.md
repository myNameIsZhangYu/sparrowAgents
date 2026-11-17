# PersonAgents 完整主流程验证报告

- 验证时间：2026-11-17
- 验证环境：Docker Compose 默认 profile + observability + monitoring + postgres
- 应用地址：`http://127.0.0.1:8000`
- 运行中服务静态 API/Admin Key：`123456`
- Langfuse / Grafana / Prometheus：已启动并纳入验证

## 总体结论

主链路可用：真实 LLM 同步对话、异步任务创建/取消、治理与安全、Web 控制台、Prometheus/Grafana 均通过。

2026-11-17 修复后复验：原 Bug-1、Bug-2、Bug-3 已修复。Langfuse 首次初始化配置问题仍未处理。

## 通过项

- `/healthz`：200，`{"status":"ok"}`
- `/readyz`：PostgreSQL、Redis 均 ok
- 真实同步 Agent 主流程：`hello` 返回 completed，真实 LLM 输出正常
- 异步任务生命周期：创建 202、轮询 200、取消 200、终态 canceled
- 超长 prompt：422
- 危险指令：Agent 拒绝执行
- 缺失/错误 API Key：401
- 缺失/错误 Admin Key：401/403
- Web 状态面板：API、PostgreSQL、Redis、队列指标正常
- Web 无 Key：前端提示「请输入 X-API-Key。」
- Web 正确 Key 同步对话：真实输出 + completed 状态
- Web 错误 Key：显示 Invalid API key
- Web 窄屏响应式：无阻断性布局问题
- Prometheus：健康，`up{job="person-agents"} = 1`
- Grafana：健康，database ok
- Langfuse：服务健康，前端登录页可访问

## 未通过项

### Bug-1：动态凭据创建后立即失效（已修复）
- 现象：Admin 使用 PostgreSQL 创建动态凭据成功，返回明文 key。
- 但 `/runs` 使用该 key 返回 401 Invalid API key。
- 根因：`src/person_agents/api/admin.py` 的创建路径使用 `PostgresCredentialStore`，而认证路径 `src/person_agents/security/auth.py` 仍查 SQLite `credentials.db`。
- 结果：Admin 创建的动态 key 无法使用，且撤销接口虽然返回 204，但真实撤销链路也未贯通。

### Bug-2：异步/API 并发槽位泄漏（已修复）
- 现象：真实 API 异步创建后取消，随后请求返回 429 Concurrent run quota exceeded。
- Redis 中发现 `run_slot:default:user_...` 残留 3 个槽位。
- 根因：`/runs/async` 在 API 层 `allow_run()`，但槽位实际释放依赖 worker；取消或 worker 异常路径下未能可靠归还。
- 影响：默认租户并发上限只有 3，泄漏后主流程会被阻塞。

### Bug-3：Web 异步任务卡片渲染失败（已修复）
- 现象：`/runs/async` 成功创建任务，浏览器轮询 `/runs/{id}` 返回 200，但 UI 始终显示「暂无任务」。
- 浏览器控制台无报错，但手动执行 `renderJobs()` 抛出：
  `TypeError: Cannot read properties of undefined (reading 'slice')`
- 根因：`src/person_agents/api/static/index.html` 的 `renderJobs()` 访问 `job.run_id.slice(...)`，而 `/runs/{run_id}` 响应模型 `RunResponse` 不包含 `run_id`。
- 影响：异步任务创建成功但用户不可见，也无法通过 UI 取消。

### 配置问题：Langfuse API key 无效
- `compose.env` 中配置了 Langfuse public/secret key，但 Langfuse 数据库 `api_keys` 表为空，且没有任何用户。
- 应用若尝试上报 trace 会因无效凭据失败。
- Langfuse UI 可访问，但未完成首次初始化。

## 测试证据

## 修复后复验

- `uv run ruff check src tests`：All checks passed
- `uv run pytest -q`：182 passed, 9 skipped
- 动态凭据：Admin 创建的 PostgreSQL key 已可通过 `/runs` 认证并返回 completed
- 三轮异步创建/取消：Redis 中 `run_slot:*` 清空
- 浏览器异步回归：任务卡片显示 running、显示完整 run_id；进入 `awaiting_confirmation` 后按契约隐藏取消按钮
- 截图：
  - `/tmp/final-async.png`
  - `/tmp/final-async-canceled.png`

- `/tmp/01-no-key.png`：无 Key 提示
- `/tmp/02-sync.png`：同步真实对话成功
- `/tmp/03-async.png`：异步创建成功（UI 未显示卡片）
- `/tmp/04-async-running.png`：异步运行
- `/tmp/05-async-canceled.png`：取消后状态仍被 UI 隐藏
- `/tmp/06-bad-key.png`：错误 Key 显示失败
- `/tmp/07-langfuse.png`：Langfuse 初始 Loading
- `/tmp/07-langfuse2.png`：Langfuse 登录页
- `/tmp/08-grafana.png`：Grafana 登录页
- `/tmp/09-prometheus.png`：Prometheus UI
