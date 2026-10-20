import logging

import structlog

from person_agents.config.settings import settings


def setup_logging() -> None:
    # structlog 与 stdlib logging 共享级别；输出格式由部署选择。
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=log_level, format="%(message)s")
    if settings.log_format == "json":
        # 生产容器通常用 JSON；本地默认保留可读彩色输出。
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            # contextvars 支持请求上下文日志；异常和 UTC 时间戳统一处理。
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )
