#!/usr/bin/env bash
set -euo pipefail

# 所有操作都基于脚本所在目录，避免从项目子目录调用时选错 Compose 文件。
cd "$(dirname "$0")"

# 默认启动全部服务：核心应用、可观测性、监控和 PostgreSQL。
profiles=(--profile default --profile observability --profile monitoring --profile postgres)

# Compose 变量插值和容器内环境都使用同一份 local/env/compose.env，避免必需变量缺失。
compose() {
  docker compose --env-file local/env/compose.env "${profiles[@]}" "$@"
}

# 检查 Docker 与 Compose 是否可用，尽早失败并给出明确原因。
if ! command -v docker >/dev/null 2>&1; then
  echo "错误：未找到 Docker，请先安装并启动 Docker。" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "错误：Docker Compose 不可用。" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "错误：Docker daemon 未运行，请先启动 Docker Desktop 或 dockerd。" >&2
  exit 1
fi

# 准备本地环境文件；首次运行时从示例复制，但必须填写 API 密钥后重试。
if [[ ! -f local/env/app.env ]]; then
  mkdir -p local/env
  if [[ -f .env.example ]]; then
    cp .env.example local/env/app.env
    echo "提示：已从 .env.example 创建 local/env/app.env，请填入 PERSON_AGENTS_API_KEY 和 PERSON_AGENTS_ADMIN_API_KEY 后重新运行。" >&2
  fi
fi

# 生成 Compose 专用环境文件，其中保存 API 密钥哈希和组件密码。
if [[ ! -s local/env/compose.env ]]; then
  bash scripts/generate_compose_env.sh local/env/compose.env
fi

# 校验 API 密钥哈希，防止应用容器启动后因缺少必需变量立即退出。
if ! grep -q '^API_KEY_HASH=' local/env/compose.env || [[ -z "$(grep '^API_KEY_HASH=' local/env/compose.env | cut -d= -f2-)" ]]; then
  echo "错误：local/env/compose.env 缺少 API_KEY_HASH，请重新生成该文件。" >&2
  exit 1
fi

# 启动数据库依赖并执行迁移，随后拉起全部 profile 中的服务。
start_services() {
  echo "==> 清理同项目遗留容器"
  compose down --remove-orphans

  # 日常启动复用本地镜像；只有在本地完全没有镜像时才构建，避免普通 start 触发代码重新打包。
  if ! docker image inspect personagents-app:latest >/dev/null 2>&1; then
    echo "==> 本地缺少应用镜像，首次构建"
    compose build app worker migrate
  else
    echo "==> 复用现有应用镜像"
  fi

  echo "==> 启动数据库并执行迁移"
  compose up -d postgres langfuse-db
  compose run --rm migrate

  echo "==> 启动全部服务"
  compose up -d
  wait_for_api
}

# 停止并移除容器与临时网络；命名卷会保留，数据不会丢失。
stop_services() {
  echo "==> 停止全部服务"
  compose down --remove-orphans
}

# 更新并重启：先停应用进程，再构建镜像和迁移数据库，最后重建应用服务。
restart_services() {
  echo "==> 清理同项目遗留容器"
  compose down --remove-orphans

  echo "==> 构建更新后的应用镜像"
  compose build app worker migrate

  echo "==> 启动数据库并执行迁移"
  compose up -d postgres langfuse-db
  compose run --rm migrate

  echo "==> 更新重启全部服务"
  compose up -d
  wait_for_api
}

# 轮询本地健康端点，确认 API 进程已可用。
wait_for_api() {
  echo "==> 等待应用健康检查"
  for _ in $(seq 1 60); do
    if curl --noproxy '*' -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
      echo "操作完成："
      echo "- API: http://127.0.0.1:8000"
      echo "- Langfuse: http://127.0.0.1:3000"
      echo "- Grafana: http://127.0.0.1:3001"
      echo "- Prometheus: http://127.0.0.1:9090"
      compose ps
      return 0
    fi
    sleep 2
  done

  echo "错误：应用健康检查超时，请查看日志：" >&2
  compose logs --tail=100 app worker
  return 1
}

# 根据第一个参数执行对应动作；无参数时输出用法。
case "${1:-}" in
  start)
    start_services
    ;;
  stop)
    stop_services
    ;;
  restart)
    restart_services
    ;;
  *)
    echo "用法：./services.sh {start|stop|restart}"
    echo "  start   一键启动全部服务（复用已有镜像）"
    echo "  stop    一键停止全部服务（保留数据卷）"
    echo "  restart 一键更新并重启应用（构建镜像并执行迁移）"
    exit 1
    ;;
esac
