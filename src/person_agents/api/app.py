import asyncio
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager

import redis
import sqlalchemy.exc
from fastapi import Depends, FastAPI, Header, HTTPException, Response

from person_agents.api.admin import router as admin_router
from person_agents.api.schemas import Provenance, RunAcceptedResponse, RunRequest, RunResponse
from person_agents.api.web import router as web_router
from person_agents.config.settings import settings
from person_agents.governance.rate_limiter import QuotaExceededError, RateLimiter
from person_agents.graph.engine import run_graph as _run_graph
from person_agents.graph.runtime import GraphServices
from person_agents.llm.fallback import LLMFallbackError
from person_agents.memory.postgres_store import PostgresStore
from person_agents.memory.sqlite_store import SQLiteStore
from person_agents.middleware import _confirmations
from person_agents.runtime.job_store import AsyncPostgresJobAdapter
from person_agents.runtime.jobs import JobManager
from person_agents.runtime.state import create_state_store
from person_agents.security.auth import SecurityError, resolve_request_context
from person_agents.security.context import RequestContext


@asynccontextmanager
async def lifespan(application: FastAPI):
    # 应用启动时构建一次默认依赖服务，注入到请求上下文，避免每个请求重建资源。
    application.state.graph_services = GraphServices.default()
    yield


app = FastAPI(title="person-agents", version="0.1.0", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(web_router)


async def current_context(x_api_key: str | None = Header(default=None)) -> RequestContext:
    try:
        # 将 API Key 解析成租户、用户、角色和会话上下文。
        return resolve_request_context(x_api_key)
    except SecurityError as exc:
        # 认证失败统一转换为 401，避免泄露内部凭证错误细节。
        raise HTTPException(status_code=401, detail=str(exc)) from exc


_context_dependency = Depends(current_context)
_pg_store: PostgresStore | None = PostgresStore() if settings.postgres_dsn else None
_store = AsyncPostgresJobAdapter(_pg_store) if _pg_store else SQLiteStore()
_jobs = JobManager(_store)
_state_store = create_state_store(settings.redis_url)
_rate_limiter = RateLimiter(
    _state_store,
    max_running_runs=settings.tenant_max_running_runs,
    daily_run_limit=settings.tenant_daily_run_limit,
)


@app.middleware("http")
async def metrics_middleware(request, call_next):
    from person_agents.observability.metrics import (
        http_request_duration_seconds,
        http_requests_total,
    )

    started = time.perf_counter()
    # 继续执行后续路由和中间件。
    response = await call_next(request)
    # 已匹配路由时使用 route 模板，避免高基数 URL 污染 Prometheus。
    route = request.scope.get("route")
    endpoint = getattr(route, "path", request.url.path)
    http_requests_total.labels(request.method, endpoint, str(response.status_code)).inc()
    http_request_duration_seconds.labels(request.method, endpoint).observe(time.perf_counter() - started)
    return response


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> Response:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    from person_agents.observability.metrics import (
        registry,
        sqlite_dead_letter_jobs,
        sqlite_queue_depth,
        sqlite_running_jobs,
    )

    dependencies: dict[str, str] = {}
    queue_depth = 0
    running_depth = 0
    dead_letter_depth = 0
    try:
        # 刷新任务队列指标，同时确认任务后端当前可查询。
        from person_agents.runtime.job_store import AsyncPostgresJobAdapter

        if isinstance(_store, AsyncPostgresJobAdapter):
            queue_depth = await _store.store.depth("queued")
            running_depth = await _store.store.depth("running")
            dead_letter_depth = await _store.store.depth("dead_letter")
        else:
            queue_depth = _store.queue_depth()
            running_depth = _store.running_depth()
            dead_letter_depth = _store.dead_letter_depth()
        backend = "postgres" if settings.postgres_dsn else "sqlite"
        dependencies[backend] = "ok"
    except (sqlite3.Error, OSError, RuntimeError, sqlalchemy.exc.SQLAlchemyError):
        dependencies["postgres" if settings.postgres_dsn else "sqlite"] = "failed"
    if settings.redis_url:
        try:
            # Redis 承载限流和取消信号，必须参与就绪检查。
            _state_store.get_json("readyz")
            dependencies["redis"] = "ok"
        except redis.RedisError:
            dependencies["redis"] = "failed"
    healthy = all(status == "ok" for status in dependencies.values())
    sqlite_queue_depth.set(queue_depth)
    sqlite_running_jobs.set(running_depth)
    sqlite_dead_letter_jobs.set(dead_letter_depth)
    body = generate_latest(registry) + f"\nreadyz_dependencies{dependencies}\n".encode()
    return Response(content=body, media_type=CONTENT_TYPE_LATEST, status_code=200 if healthy else 503)


@app.get("/metrics")
async def metrics() -> Response:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    from person_agents.observability.metrics import registry

    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


@app.post("/confirmations/{token}/confirm")
async def confirm_tool(token: str, context: RequestContext = _context_dependency) -> dict[str, bool]:
    # 读取确认令牌，但不消费它；真正的工具执行方稍后仍需使用同一令牌。
    confirmation = _confirmations.get(token)
    if confirmation is None or confirmation.owner_user_id != context.user_id or confirmation.tenant_id != context.tenant_id:
        raise HTTPException(status_code=404, detail="Confirmation not found")
    return {"confirmed": True}


@app.post("/confirmations/{token}/reject")
async def reject_tool(token: str, context: RequestContext = _context_dependency) -> dict[str, bool]:
    # 拒绝时立即消费令牌，防止令牌被重复确认。
    confirmation = _confirmations.consume(token)
    if confirmation is None or confirmation.owner_user_id != context.user_id or confirmation.tenant_id != context.tenant_id:
        raise HTTPException(status_code=404, detail="Confirmation not found")
    return {"rejected": True}


@app.post("/runs", response_model=RunResponse)
async def create_run(
    request: RunRequest,
    context: RequestContext = _context_dependency,
) -> RunResponse:
    # 请求会话优先于认证上下文中的新会话，便于客户端连续跟踪同一任务。
    session_id = request.session_id or context.session_id
    scoped_context = RequestContext(user_id=context.user_id, tenant_id=context.tenant_id, session_id=session_id)
    try:
        # 先检查累计配额，再占用并发槽位。
        _rate_limiter.check_usage(context.user_id, context.tenant_id)
        slot_id = _rate_limiter.allow_run(context.user_id, context.tenant_id)
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    try:
        # 同步接口限制最长等待时间，超时后释放槽位并返回 504。
        result = await asyncio.wait_for(
            _run_graph(request.prompt, session_id=session_id, context=scoped_context, services=getattr(app.state, "graph_services", None)),
            timeout=settings.run_timeout_s,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Agent run timed out") from exc
    except LLMFallbackError as exc:
        raise HTTPException(status_code=503, detail="LLM providers unavailable") from exc
    finally:
        # 无论成功、超时还是异常，都必须归还并发配额。
        _rate_limiter.release_run_slot(slot_id, context.user_id, context.tenant_id)

    tool_results = result.get("tool_results", [])
    # 输出取最后一个执行结果；确认请求需要隐藏业务输出并返回确认令牌。
    output = tool_results[-1].get("output") if tool_results else None
    confirmation_token = None
    status = result.get("status") or "completed"
    if isinstance(output, dict) and output.get("status") == "confirmation_required":
        status = "confirmation_required"
        confirmation_token = output["confirmation_token"]
        output = None
    return RunResponse(session_id=session_id, status=status, output=output, confirmation_token=confirmation_token)


@app.post("/runs/async", response_model=RunAcceptedResponse, status_code=202)
async def create_async_run(
    request: RunRequest,
    context: RequestContext = _context_dependency,
) -> RunAcceptedResponse:
    session_id = request.session_id or context.session_id
    try:
        _rate_limiter.check_usage(context.user_id, context.tenant_id)
        _rate_limiter.allow_run(context.user_id, context.tenant_id)
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    slot_id = None
    try:
        _rate_limiter.check_usage(context.user_id, context.tenant_id)
        slot_id = _rate_limiter.allow_run(context.user_id, context.tenant_id)
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    finally:
        if slot_id:
            _rate_limiter.release_run_slot(slot_id, context.user_id, context.tenant_id)
    provenance = await _jobs.default_provenance()
    if _pg_store:
        run_id = str(uuid.uuid4())
        await _pg_store.create_run(run_id, request.prompt, context.user_id, context.tenant_id, session_id, provenance=provenance)
    else:
        run_id = _jobs.create(request.prompt, context.user_id, context.tenant_id, session_id, provenance)
    return RunAcceptedResponse(run_id=run_id, status="queued", session_id=session_id)


@app.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, context: RequestContext = _context_dependency) -> RunResponse:
    run = await _pg_store.get_run(run_id, context.user_id, context.tenant_id) if _pg_store else _jobs.get(run_id, context.user_id, context.tenant_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunResponse(
        session_id=run["session_id"],
        status=run["status"],
        output=run["output"],
        confirmation_token=run["confirmation_token"],
        provenance=Provenance(
            agent_version=run.get("agent_version") or settings.agent_version,
            prompt_version=run.get("prompt_version") or settings.prompt_version,
            tool_config_version=run.get("tool_config_version") or settings.tool_config_version,
            policy_version=run.get("policy_version") or settings.policy_version,
        ),
    )


@app.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, context: RequestContext = _context_dependency) -> RunResponse:
    run = await _pg_store.get_run(run_id, context.user_id, context.tenant_id) if _pg_store else _jobs.get(run_id, context.user_id, context.tenant_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] not in ("queued", "running"):
        raise HTTPException(status_code=409, detail="Run is not cancelable")
    _state_store.set_json(f"cancel:{run_id}", True, ttl_seconds=3600)
    if _pg_store:
        await _pg_store.update_run(run_id, context.user_id, context.tenant_id, "canceled")
    else:
        _jobs.update(run_id, context.user_id, context.tenant_id, "canceled")
    return RunResponse(session_id=run["session_id"], status="canceled")
