# 生产级验证方案

## 1. 部署与基础验证

```bash
bash -n services.sh
docker compose --env-file local/env/compose.env --profile default --profile observability --profile monitoring --profile postgres config --quiet
./services.sh start
docker compose --env-file local/env/compose.env --profile default --profile observability --profile monitoring --profile postgres ps
curl --noproxy '*' -fsS http://127.0.0.1:8000/healthz
curl --noproxy '*' -fsS http://127.0.0.1:8000/readyz | grep readyz_dependencies
```

验收：

- 全部核心容器 healthy 或 Up。
- `/healthz` 返回 `{"status":"ok"}`。
- `/readyz` 中 `sqlite`/`postgres` 与 `redis` 均为 `ok`。

## 2. Web 控制台验证

打开 `http://127.0.0.1:8000/`。

| 用例 | 步骤 | 通过标准 |
|---|---|---|
| 状态面板 | 打开首页，等待 5 秒 | API/SQLite/Redis 显示正常，指标非 `—` |
| 未授权提示 | 不输入 Key，点击发送 | 页面提示“请输入 X-API-Key” |
| 同步对话 | 输入 Key 和“hello”，发送 | 返回 Agent 输出；LLM 故障时展示“执行失败” |
| 异步任务 | 点击“异步执行” | 创建 `run_id`，3 秒内轮询状态 |
| 取消任务 | 任务 `queued/running` 时取消 | 状态变为 `canceled` |
| 错误处理 | 填错 Key 后执行 | 页面展示 401，不泄露堆栈 |
| 可用性 | 窗口缩到 950px 以下 | 移动端可读、按钮可点击 |

## 3. API 功能验证

```bash
KEY='<PERSON_AGENTS_API_KEY>'

curl --noproxy '*' -sS -X POST http://127.0.0.1:8000/runs \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"prompt":"hello"}'

curl --noproxy '*' -sS -X POST http://127.0.0.1:8000/runs/async \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"prompt":"long running task"}'

curl --noproxy '*' -fsS http://127.0.0.1:8000/runs/<run_id> \
  -H "X-API-Key: $KEY"

curl --noproxy '*' -sS -X POST http://127.0.0.1:8000/runs/<run_id>/cancel \
  -H "X-API-Key: $KEY"
```

补充用例：

- 缺少 API Key：`401`。
- 错误 API Key：`401`。
- 超长 prompt：`422`。
- LLM 全链路失败：`503`，响应为 `{"detail":"LLM providers unavailable"}`。
- 确认令牌 `confirm` 成功后不可二次使用。
- `reject` 后不应执行工具。
- Admin 创建/轮换/撤销凭据后，旧 Key 立即失效，新 Key 可用。

## 4. 治理与安全

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8000/metrics | grep -E 'agent_(runs|loop_breaks)_total|tool_calls_total|llm_calls_total'
```

必须验证：

- 危险命令被策略拒绝。
- 只读授权用户执行写操作时被拒绝。
- 租户并发配额达到阈值后返回限流错误。
- 确认令牌不可预测、只能消费一次、过期后失效。
- 审计日志包含租户/用户/动作/结果，且敏感字段已脱敏。
- 日志不包含 API Key、密码、完整 Postgres DSN。

## 5. 数据与恢复

```bash
docker exec personagents-redis-1 redis-cli ping
docker exec personagents-postgres-1 pg_isready -U person_agents -d person_agents
./services.sh restart
./services.sh stop
./services.sh start
```

验收：

- 重启前创建的历史 run 在重启后仍可查询。
- PostgreSQL/SQLite 数据卷重启后保留。
- Redis AOF 开启；限流桶、确认令牌行为符合预期。
- `stop` 后端口不可访问；`start` 后恢复。

## 6. 可观测性

| 服务 | 地址 | 验收 |
|---|---|---|
| Metrics | `http://127.0.0.1:8000/metrics` | 包含 HTTP、run、agent、tool、LLM、队列指标 |
| Prometheus | `http://127.0.0.1:9090` | `up{job="personagents"}` 为 1 |
| Grafana | `http://127.0.0.1:3001` | 使用默认数据源查看 API/Agent 面板 |
| Langfuse | `http://127.0.0.1:3000` | 打开页面；trace 会话能关联 prompt/output |

Prometheus 可用：

```bash
curl --noproxy '*' -fsS http://127.0.0.1:9090/-/healthy
curl --noproxy '*' -fsS http://127.0.0.1:8000/metrics
```

## 7. 并发、超时与稳定性

```bash
for i in $(seq 1 10); do
  curl --noproxy '*' -sS -X POST http://127.0.0.1:8000/runs/async \
    -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
    -d '{"prompt":"stress test '"$i"'"}' &
done
wait
```

验收：

- 超过租户并发配额的请求被拒绝，而不是击穿下游。
- 所有任务终态为 `completed/failed/canceled/dead_letter`，不允许长期 `running`。
- 长任务触发循环守卫后返回降级结果并增加 `agent_loop_breaks_total`。
- worker 重启后未到期 lease 可回收，任务不丢失。
- 同步 LLM 超时有上限，API 连接不被长期占满。

## 8. 生产发布门禁

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check src tests
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q
git diff --check
```

建议补充外部环境验证：

- 真实 LLM E2E。
- LangGraph 编译图 E2E。
- PostgreSQL 并发 claim/cancel。
- Docker sandbox 工具执行。
- 备份/恢复演练。
- 定时备份成功性告警。

## 9. 最小上线清单

- [ ] CI 全绿。
- [ ] 生产配置校验通过。
- [ ] 镜像版本固定且可追溯。
- [ ] API Key 仅保存哈希。
- [ ] Admin Key 与用户 Key 分离。
- [ ] 迁移已成功执行。
- [ ] 健康检查、就绪检查、指标、日志全部可用。
- [ ] 限流、审计、确认机制已验证。
- [ ] PostgreSQL/Redis 持久化卷已备份。
- [ ] 回滚版本明确。
