# PersonAgents

生产级个人智能体系统，支持多 Agent 编排、MCP 工具层、持久化记忆、权限治理、可观测性和评估框架。

## 架构总览

```
┌─────────────────────────────────────────────────────┐
│                    CLI (Typer + Rich)                │
│      serve / run / chat / eval / doctor / version      │
└────────────────────────┬────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────┐
│              LangGraph 编排引擎                      │
│  intent → planner → executor → critic → memory_write│
└──┬────────────┬────────────┬────────────┬───────────┘
   ▼            ▼            ▼            ▼
┌──────┐  ┌──────────┐  ┌─────────┐  ┌──────────────┐
│ 意图  │  │   LLM    │  │  工具层  │  │   记忆系统    │
│ 识别  │  │ 降级链   │  │MCP+沙箱 │  │SQLite+Chroma │
└──────┘  └──────────┘  └────┬────┘  └──────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────┐
│                    治理层                            │
│        权限策略 · 限流 · 审计日志                     │
└────────────────────────┬────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────┐
│                    可观测性                          │
│      Langfuse (自托管) · structlog · 成本追踪        │
└─────────────────────────────────────────────────────┘
```

## 核心功能

### 多 Agent 编排（LangGraph）

使用 LangGraph 状态机驱动 Agent 循环：

| 节点 | 职责 |
|---|---|
| **intent** | 将用户输入分类为 `chat` / `task` / `memory_write` / `tool_use` |
| **planner** | 注入工具 schema 与记忆上下文，输出结构化步骤 |
| **executor** | 按步骤调用注册表工具或 LLM，工具参数来自规划结果 |
| **critic** | 评估执行结果，返回 `success` / `retry` / `fail` |
| **memory_write** | 成功后将经验写入长期记忆 |

支持条件路由、失败重试（最多 3 次）、SQLite 检查点持久化。

### LLM 调用与降级链

统一 LLM 接口，支持多种 provider 自动降级：

```
主模型（如 glm-5.3-flash）
  ↓ 超时 / 429 / 5xx
降级模型（如 gpt-4o-mini）
  ↓ 全部失败
本地 Ollama（兜底）
  ↓ 仍失败
返回结构化错误
```

- 连接超时 5s 显式设置（`httpx.Timeout`），避免挂起
- 无 API Key 时自动跳过 OpenAI provider
- 本地 Ollama 先做健康预检（`/api/tags`）
- 每级降级自动记录 `llm_degraded` 日志

### 记忆系统

| 层 | 存储 | 用途 |
|---|---|---|
| **短期** | 内存 | 当前对话上下文 |
| **长期** | SQLite（WAL 模式） | 用户偏好、事实、决策 |
| **语义** | ChromaDB | 向量相似度检索 |

- SQLite WAL 模式保证并发安全
- ChromaDB 零配置本地持久化
- Alembic 管理 schema 迁移
- Critic 评估成功后自动写入经验记忆

### MCP 工具层

统一工具注册表（`ToolRegistry`），支持 OpenAI function calling 格式：

- **filesystem**：沙箱内读/写/列/删（路径逃逸保护）
- **Docker sandbox**：一次性容器执行 shell，限制网络/CPU/内存
- 所有工具调用经过权限策略校验和审计日志

### 治理（Governance）

| 模块 | 功能 |
|---|---|
| **PolicyEngine** | 权限分级（read/write/shell/network）+ 危险命令黑名单（regex 匹配） |
| **RateLimiter** | 令牌桶限流（LLM 和工具分别独立） |
| **AuditLogger** | SQLite/PostgreSQL 审计日志，记录每次工具调用的输入/输出/状态 |

危险命令检测支持变形绕过（如 `rm -r -f /`、`git push --force`）。

### 可观测性

- **Langfuse**（自托管 Docker）: 记录每次 LLM 调用的 input/output/token/延迟/降级状态，并按 `intent` / `planner` / `executor` / `critic` 生成嵌套节点 span
- **structlog**: 结构化 JSON 日志，支持 console/JSON 切换
- **CostTracker**: 按 session 追踪 token 消耗和调用次数

## 快速开始

### 1. 安装依赖

```bash
uv sync --all-extras
```

### 2. 配置环境变量

```bash
cp .env.example local/env/app.env
```

编辑 `local/env/app.env`：

```env
OPENAI_API_KEY=your-key-here
OPENAI_BASE_URL=https://api.openai.com/v1   # 或本地代理
OPENAI_MODEL=gpt-4o                         # 或任意兼容模型
```

### 3. 运行

```bash
# 单次提问
uv run person-agents run "你好"

# 交互式对话
uv run person-agents chat

# 评估（baseline 用例，可输出 JSON 报告）
uv run person-agents eval --output eval-report.json

# 健康检查（LLM API / 数据目录 / Ollama 连通性）
uv run person-agents doctor
```

### 4. 启动 Langfuse 监控（可选）

```bash
docker compose up -d
# 构建并启动应用与自托管监控栈（生产建议先移除 app 服务的 --help 命令）
docker compose up -d --build app

# 打开 http://localhost:3000 → 注册账号 → 创建项目 → 获取 API Key
# 将 pk-lf-xxx / sk-lf-xxx 填入 local/env/app.env 的 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
```

## 测试

```bash
# 运行全部 23 个测试（memory / tools / governance / fallback）
uv run pytest -q

# 代码检查
uv run ruff check src tests
```

## 项目结构

```
src/person_agents/
├── cli.py                 # Typer 入口（version/doctor/run/chat/eval）
├── config/
│   ├── settings.py        # pydantic-settings 多环境配置
│   └── fallback.yaml      # LLM 降级链配置
├── llm/
│   ├── client.py          # 统一 LLM 调用接口（OpenAI + Ollama）
│   ├── fallback.py        # 降级链（健康预检 + 重试）
│   └── router.py          # 简单/复杂任务路由
├── memory/
│   ├── sqlite_store.py    # SQLite WAL 存储（CRUD + session）
│   ├── vector_store.py    # ChromaDB 向量检索
│   └── migrations/        # Alembic schema 迁移
├── graph/
│   ├── state.py           # AgentState TypedDict
│   ├── nodes.py           # 意图/规划/执行/评估/记忆 节点
│   ├── edges.py           # 条件路由（含 max-retry）
│   └── checkpoint.py      # SQLite 检查点
├── tools/
│   ├── mcp_filesystem.py  # 沙箱文件操作（路径逃逸保护）
│   ├── registry.py        # 工具注册表（OpenAI schema）
│   ├── sandbox.py         # Docker 沙箱执行器
│   └── mcp_client.py      # MCP 协议客户端
├── governance/
│   ├── policy.py          # 权限 + 危险命令 regex 黑名单
│   ├── rate_limiter.py    # 令牌桶限流
│   ├── audit.py           # SQLite 审计日志
│   └── audit_store.py     # PostgreSQL 审计存储与后端选择
├── observability/
│   ├── langfuse.py        # Langfuse trace 与嵌套节点 span
│   ├── logging.py         # structlog 配置
│   └── cost.py            # token 成本追踪
└── eval/
    ├── runner.py          # 评估运行器
    └── cases/             # 基准测试用例
```

## Docker 服务

| 服务 | 端口 | 用途 |
|---|---|---|
| app | — | 编译后的 LangGraph agent 应用 |
| langfuse | 3000 | Web UI + API |
| langfuse-db | 5432 | PostgreSQL（元数据） |
| langfuse-clickhouse | 8123/9000 | ClickHouse（trace 存储） |
| langfuse-minio (SeaweedFS) | 9000 | S3 兼容对象存储 |
| langfuse-redis | 6379 | 队列（BullMQ） |
| langfuse-worker | — | Langfuse 后台任务处理 |

## 技术栈

| 组件 | 选型 | 版本 |
|---|---|---|
| Python | ≥3.11 | — |
| 编排 | LangGraph | ≥0.2 |
| 向量库 | ChromaDB | ≥0.5 |
| 数据库 | SQLite + Alembic | — |
| LLM | OpenAI SDK（兼容任意 base_url） | ≥1.0 |
| 监控 | Langfuse | ≥2.0（自托管） |
| CLI | Typer + Rich | ≥0.9 |
| 沙箱 | Docker | — |
| 校验 | Pydantic v2 | ≥2.0 |

## 工程化与部署

### API 服务

异步运行：

```bash
curl -X POST http://localhost:8000/runs/async -H 'X-API-Key: your-key' -H 'Content-Type: application/json' -d '{"prompt":"What is 2+2?"}'
curl http://localhost:8000/runs/<run_id> -H 'X-API-Key: your-key'
```

Run 状态包括 `queued`、`running`、`completed`、`failed`、`awaiting_confirmation`、`dead_letter`。`person-agents worker` 使用 lease 消费任务，执行失败会按指数退避重试；超过 `PERSON_AGENTS_RUN_MAX_ATTEMPTS` 后进入 `dead_letter`。配置 `PERSON_AGENTS_REDIS_URL` 后，confirmation、限流和租户配额状态跨进程共享。

`/metrics` 暴露 Prometheus 指标：

- `http_requests_total`
- `http_request_duration_seconds`
- `run_jobs_total`
- `run_attempts_total`
- `run_dead_letter_total`
- `tool_calls_total`
- `llm_calls_total`
- `sqlite_queue_depth`
- `sqlite_running_jobs`
- `sqlite_dead_letter_jobs`

`/readyz` 检查 SQLite；配置 Redis 后同时检查 Redis。该 endpoint 只暴露依赖状态和队列 gauge，不暴露 prompt、output 或密钥。

`POST /runs` 使用 `X-API-Key` 认证。`API_KEY_HASH` 保存的是 API key 的 SHA-256：

```bash
printf -v API_KEY_HASH '%s' "$(printf 'your-key' | sha256sum | cut -d' ' -f1)"
curl -X POST http://localhost:8000/runs \
  -H 'X-API-Key: your-key' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"What is 2+2?"}'
```

### 生产配置校验

```bash
uv run person-agents doctor --production
```

该命令校验 API key、Redis、LLM、Langfuse、secret 长度、重复值和默认凭据；任何失败都会以 exit code 2 退出。

### PostgreSQL profile

当前运行队列、worker lease、审计、credentials 与 config registry 支持通过 `PERSON_AGENTS_POSTGRES_DSN=postgresql+asyncpg://user:password@postgres:5432/person_agents` 启用 PostgreSQL。启动数据库并执行 schema bootstrap：

```bash
POSTGRES_USER=person_agents POSTGRES_PASSWORD=strong-password POSTGRES_DB=person_agents \
  docker compose --profile postgres up -d postgres
```

PostgreSQL profile 会先运行 `migrate` 一次性服务，再启动 app 与 worker。可用 `scripts/verify_production_stack.sh` 执行基础生产栈验证。

危险工具会返回 `status=confirmation_required` 和一次性 `confirmation_token`。当前 API 核心已实现认证、租户上下文、用户隔离、schema 校验、确认工作流和 `/healthz`；批准/拒绝 endpoint 仍在下一批实现中。

- **租户配额**：默认最多 3 个并发 Run、1000 个每日 Run；可通过 `PERSON_AGENTS_TENANT_*` 环境变量调整。
- **运行溯源**：每个 Run 记录 agent、prompt、工具配置和策略版本，并通过 run response 的 `provenance` 返回。

- **应用镜像**：`Dockerfile.app` 使用非 root 运行时和独立数据卷。
- **CI**：`.github/workflows/ci.yml` 在 push/PR 时执行 `ruff check` 和 `pytest`。
- **治理配置**：通过 `PERSON_AGENTS_GRANTED_PERMISSIONS=read,write` 控制默认权限；生产环境不应直接授予 `shell`。
- **沙箱**：shell 工具使用一次性容器，默认禁网并限制 CPU / 内存。
- **数据持久化**：agent 数据、Langfuse 元数据、trace 与对象存储均使用 named volumes。
- **监控告警**：`docker compose --profile monitoring up -d prometheus grafana` 启动 Prometheus 与 Grafana；Prometheus 配置位于 `deploy/prometheus/`，Grafana 仪表板自动挂载。
- **评估**：`person-agents eval` 直接调用当前 fallback chain，并生成 JSON pass rate / token 报告。
- **评估门禁**：`person-agents eval --threshold 0.7` 保存结果并执行阈值校验；CI 可通过 repository variable `RUN_EVAL_GATE=true` 启用。
- **生产核心**：FastAPI、API key 认证、tenant/user 隔离、JSON Schema 工具校验、危险工具人工确认和 Docker 服务入口已接入。

### 生产部署检查

1. 生成强随机 API key，只保存 SHA-256 到 `PERSON_AGENTS_API_KEY_HASH`。
2. 必须配置 `PERSON_AGENTS_REDIS_URL`，生产环境依赖 Redis 保存跨进程状态。
3. 使用强随机凭据替换 Langfuse 示例配置；不要将真实 secret 提交到仓库。
4. 定期备份 `app_data`（SQLite / Chroma / workspace）和 `redis_data`；恢复前先停止 app 与 worker。
5. 观察 `/readyz`、`/metrics`、`sqlite_dead_letter_jobs` 与 `run_dead_letter_total`。
6. 升级时先停止 worker，等待 `running` 任务清空，再替换镜像并重启服务。

## License

MIT

## 外部验证开关

- `RUN_LANGGRAPH_E2E=true` 启用被沙箱跳过的 LangGraph 编译图 / 完整执行测试。
- `RUN_SANDBOX_E2E=true` 启用 Docker sandbox E2E 测试。
- `PERSON_AGENTS_TEST_POSTGRES_DSN=postgresql+asyncpg://...` 启用 PostgreSQL queue/runtime store 集成测试。

### Docker build network

If dependency downloads stall during image build, use the offline wheelhouse flow:

```bash
PERSON_AGENTS_API_KEY='your-api-key' \
PERSON_AGENTS_ADMIN_API_KEY='your-admin-key' \
./scripts/generate_compose_env.sh local/env/compose.env

./scripts/prepare_wheelhouse.sh local/cache/wheelhouse
docker compose --env-file local/env/compose.env --profile default build app
docker compose --env-file local/env/compose.env --profile default up -d
```

If the OpenAI-compatible endpoint, Ollama, or other service runs on the host, rewrite `127.0.0.1` and
`localhost` to the WSL gateway address in `local/env/compose.env`; `host.docker.internal` may need
`extra_hosts: ["host.docker.internal:host-gateway"]` depending on Docker Desktop/WSL2 networking.

### Local LLM proxy relay

When the OpenAI-compatible proxy binds only to `127.0.0.1`, the app container cannot use
`host.docker.internal` directly. Compose includes a persistent host-network relay for this case:

```env
LLM_RELAY_PORT=18788
LLM_RELAY_TARGET=127.0.0.1
LLM_RELAY_TARGET_PORT=8788
OPENAI_BASE_URL=http://host.docker.internal:18788/v1
```

The relay is part of the `default` profile and uses `restart: unless-stopped`. It forwards
container traffic from the host port `LLM_RELAY_PORT` to the host-only proxy on
`LLM_RELAY_TARGET:LLM_RELAY_TARGET_PORT`.
