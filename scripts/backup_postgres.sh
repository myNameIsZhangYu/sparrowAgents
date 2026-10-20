#!/usr/bin/env bash
set -euo pipefail

output_dir="${1:?set backup output directory}"
mkdir -p "$output_dir"

POSTGRES_USER="${POSTGRES_USER:?set POSTGRES_USER}" \
POSTGRES_DB="${POSTGRES_DB:?set POSTGRES_DB}" \
docker compose --profile postgres exec -T postgres \
  pg_dump --format=custom --file=- "${POSTGRES_DB}" >"$output_dir/person_agents_$(date +%Y%m%d_%H%M%S).dump"

printf 'backup written to %s\n' "$output_dir"
