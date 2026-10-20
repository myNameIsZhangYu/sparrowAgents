#!/usr/bin/env bash
set -euo pipefail

export POSTGRES_USER="${POSTGRES_USER:-person_agents}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}"
export POSTGRES_DB="${POSTGRES_DB:-person_agents}"
export GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD}"
export LANGFUSE_SALT="${LANGFUSE_SALT:?set LANGFUSE_SALT}"
export LANGFUSE_ENCRYPTION_KEY="${LANGFUSE_ENCRYPTION_KEY:?set LANGFUSE_ENCRYPTION_KEY}"
export LANGFUSE_S3_ACCESS_KEY_ID="${LANGFUSE_S3_ACCESS_KEY_ID:?set LANGFUSE_S3_ACCESS_KEY_ID}"
export LANGFUSE_S3_SECRET_ACCESS_KEY="${LANGFUSE_S3_SECRET_ACCESS_KEY:?set LANGFUSE_S3_SECRET_ACCESS_KEY}"

docker compose --profile postgres up -d --build migrate postgres
docker compose --profile postgres up -d --build app worker

for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8000/readyz >/dev/null; then
    break
  fi
  sleep 2
done

curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/metrics >/dev/null
echo "production stack verified"
