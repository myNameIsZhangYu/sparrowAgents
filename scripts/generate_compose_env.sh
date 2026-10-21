#!/usr/bin/env bash
set -euo pipefail

if [[ -f "${APP_ENV_FILE:-local/env/app.env}" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "${APP_ENV_FILE:-local/env/app.env}"
  set +a
fi

PERSON_AGENTS_API_KEY="${PERSON_AGENTS_API_KEY:?set PERSON_AGENTS_API_KEY}"
PERSON_AGENTS_ADMIN_API_KEY="${PERSON_AGENTS_ADMIN_API_KEY:?set PERSON_AGENTS_ADMIN_API_KEY}"

compose_openai_base_url="${OPENAI_BASE_URL:-https://api.openai.com/v1}"
compose_openai_base_url="${compose_openai_base_url//127.0.0.1/host.docker.internal}"
compose_openai_base_url="${compose_openai_base_url//localhost/host.docker.internal}"

output="${1:-local/env/compose.env}"
umask 077
cat >"$output" <<EOF
OPENAI_API_KEY=${OPENAI_API_KEY:-}
OPENAI_BASE_URL=${compose_openai_base_url}
OPENAI_MODEL=${OPENAI_MODEL:-gpt-4o}
OLLAMA_BASE_URL=${OLLAMA_BASE_URL:-http://host.docker.internal:11434}
LOG_LEVEL=${LOG_LEVEL:-INFO}
LOG_FORMAT=${LOG_FORMAT:-console}
LANGFUSE_HOST=http://langfuse:3000
NEXTAUTH_SECRET=${NEXTAUTH_SECRET:-$(openssl rand -hex 32)}
LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY:-}
LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY:-}
API_KEY_HASH=$(printf '%s' "$PERSON_AGENTS_API_KEY" | sha256sum | cut -d' ' -f1)
ADMIN_API_KEY_HASH=$(printf '%s' "$PERSON_AGENTS_ADMIN_API_KEY" | sha256sum | cut -d' ' -f1)
POSTGRES_USER=person_agents
POSTGRES_PASSWORD=$(openssl rand -hex 24)
POSTGRES_DB=person_agents
GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)
LANGFUSE_SALT=$(openssl rand -hex 32)
LANGFUSE_ENCRYPTION_KEY=$(openssl rand -hex 32)
LANGFUSE_S3_ACCESS_KEY_ID=$(openssl rand -hex 16)
LANGFUSE_S3_SECRET_ACCESS_KEY=$(openssl rand -hex 24)
UV_INDEX_URL=${UV_INDEX_URL:-}
UV_HTTP_TIMEOUT=${UV_HTTP_TIMEOUT:-120}
RUNTIME_SECRET=${RUNTIME_SECRET:-$(openssl rand -base64 32 | tr '+/' '-_')}
HTTP_PROXY=${HTTP_PROXY:-}
HTTPS_PROXY=${HTTPS_PROXY:-}
NO_PROXY=${NO_PROXY:-localhost,127.0.0.1}
EOF

printf 'wrote %s\n' "$output"
printf 'PERSON_AGENTS_API_KEY=%s\n' "$PERSON_AGENTS_API_KEY"
printf 'PERSON_AGENTS_ADMIN_API_KEY=%s\n' "$PERSON_AGENTS_ADMIN_API_KEY"
