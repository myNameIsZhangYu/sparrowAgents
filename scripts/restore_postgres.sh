#!/usr/bin/env bash
set -euo pipefail

backup_file="${1:?set backup file}"
POSTGRES_USER="${POSTGRES_USER:?set POSTGRES_USER}" \
POSTGRES_DB="${POSTGRES_DB:?set POSTGRES_DB}" \
docker compose --profile postgres exec -T postgres \
  pg_restore --clean --if-exists --dbname="${POSTGRES_DB}" <"$backup_file"

printf 'restored %s\n' "$backup_file"
