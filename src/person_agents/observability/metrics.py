
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

registry = CollectorRegistry()

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
    registry=registry,
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    registry=registry,
)
run_jobs_total = Counter(
    "run_jobs_total",
    "Run job lifecycle events",
    ["status"],
    registry=registry,
)
agent_runs_total = Counter(
    "agent_runs_total",
    "Agent node lifecycle events",
    ["agent", "status"],
    registry=registry,
)
agent_run_duration_seconds = Histogram(
    "agent_run_duration_seconds",
    "Agent node latency",
    ["agent"],
    registry=registry,
)
tool_calls_total = Counter(
    "tool_calls_total",
    "Governed tool calls",
    ["tool", "status"],
    registry=registry,
)
llm_calls_total = Counter(
    "llm_calls_total",
    "Governed LLM calls",
    ["status", "degraded"],
    registry=registry,
)

sqlite_queue_depth = Gauge("sqlite_queue_depth", "Queued SQLite jobs", registry=registry)
sqlite_running_jobs = Gauge("sqlite_running_jobs", "Running SQLite jobs", registry=registry)
sqlite_dead_letter_jobs = Gauge("sqlite_dead_letter_jobs", "Dead-letter SQLite jobs", registry=registry)
