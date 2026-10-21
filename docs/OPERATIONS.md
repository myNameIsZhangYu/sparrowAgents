# Production Operations Runbook

## Startup

1. Create `local/env/app.env` with the required application variables below, then run `./services.sh start`
   or `bash scripts/generate_compose_env.sh local/env/compose.env` to create the private Compose file.
   `local/env/compose.env` is git-ignored and contains generated passwords, API key hashes, and
   service credentials. To retrieve Grafana's password, run:
   `grep '^GRAFANA_ADMIN_PASSWORD=' local/env/compose.env`.
2. Run `scripts/verify_production_stack.sh`.
3. Check `/healthz`, `/readyz`, and `/metrics`.

## Required environment variables

| Variable | Purpose |
|---|---|
| `PERSON_AGENTS_API_KEY_HASH` | SHA-256 hash of the production API key |
| `PERSON_AGENTS_ADMIN_API_KEY_HASH` | SHA-256 hash of the admin API key |
| `PERSON_AGENTS_REDIS_URL` | Cross-process state and queue coordination |
| `PERSON_AGENTS_POSTGRES_DSN` | PostgreSQL async DSN for the Postgres profile |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | PostgreSQL service bootstrap |
| `LANGFUSE_SALT` | Langfuse encryption salt |
| `LANGFUSE_ENCRYPTION_KEY` | Langfuse encryption key |
| `LANGFUSE_S3_ACCESS_KEY_ID`, `LANGFUSE_S3_SECRET_ACCESS_KEY` | Langfuse object storage credentials |
| `GRAFANA_ADMIN_PASSWORD` | Grafana administrator password |

## PostgreSQL maintenance

- Back up with `pg_dump --format=custom`.
- Restore with `pg_restore --clean --if-exists`.
- Stop app and worker before restoring.
- Run `person-agents migrate` after restoring an empty database.

### Backup and restore drill

```bash
set -a
source local/env/compose.env
set +a

./scripts/backup_postgres.sh /tmp/person-agents-backups
docker compose --env-file local/env/compose.env --profile postgres stop app worker
./scripts/restore_postgres.sh /tmp/person-agents-backups/person_agents_TIMESTAMP.dump
docker compose --env-file local/env/compose.env --profile postgres up -d app worker
curl -fsS http://localhost:8000/readyz
```

## Upgrades

1. Stop the worker.
2. Wait for `running` jobs to drain.
3. Back up PostgreSQL, Redis, and Chroma volumes.
4. Build and tag the new image with an immutable version.
5. Run `person-agents migrate`.
6. Start app and worker.
7. Verify `/readyz` and `/metrics`.

## Rollback

1. Stop app and worker.
2. Restore the previous immutable image tag.
3. Restore the matching database backup if the schema changed.
4. Restart services and check readiness.

## Incident response

- Queue depth growing: inspect worker logs and PostgreSQL locks.
- Dead letter increasing: inspect `runs.error`, fix the root cause, requeue manually after approval.
- Redis unavailable: requests fail closed; restore Redis before accepting runs.
- Langfuse unavailable: agent execution can continue, but trace loss must be recorded in the incident report.
