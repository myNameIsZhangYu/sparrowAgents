import structlog

from person_agents.config.settings import settings

logger = structlog.get_logger()


class SandboxResult:
    # 沙箱只返回进程退出码和受限输出，不暴露容器句柄。
    exit_code: int
    stdout: str
    stderr: str


class DockerSandbox:
    def __init__(self) -> None:
        # Docker client 延迟创建，避免模块导入时要求 Docker daemon。
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    async def execute(self, command: str, workdir: str = "/workspace", network_disabled: bool = True) -> SandboxResult:
        client = self._ensure_client()
        container_id: str | None = None
        # 强制资源上限；默认禁网，防止模型生成的命令访问外部服务。
        host_config = client.api.create_host_config(
            network_mode="none" if network_disabled else "bridge",
            mem_limit=f"{settings.sandbox_memory_mb}m",
            nano_cpus=int(settings.sandbox_cpus * 1e9),
            binds={str(settings.data_dir / "workspace"): {"bind": "/workspace", "mode": "rw"}},
        )
        try:
            container = client.api.create_container(
                image=settings.sandbox_image,
                command=["sh", "-c", command],
                host_config=host_config,
                working_dir=workdir,
                user="nobody",
            )
            container_id = container["Id"]
            # 等待进程结束；超时由 Docker wait 的 timeout 控制。
            client.api.start(container_id)
            exit_info = client.api.wait(container_id, timeout=settings.sandbox_timeout_s)
            stdout_raw = client.api.logs(container_id, stdout=True, stderr=False)
            stderr_raw = client.api.logs(container_id, stdout=False, stderr=True)
            stdout = stdout_raw.decode("utf-8", errors="replace")
            stderr = stderr_raw.decode("utf-8", errors="replace")
            result = SandboxResult()
            result.exit_code = exit_info.get("StatusCode", -1)
            result.stdout = stdout
            result.stderr = stderr
            logger.info("sandbox_executed", command=command, exit_code=result.exit_code)
            return result
        finally:
            if container_id:
                try:
                    # 无论成功、失败或超时，都强删一次性容器。
                    client.api.remove_container(container_id, force=True)
                except Exception:
                    logger.warning("sandbox_cleanup_failed", container_id=container_id, exc_info=True)
