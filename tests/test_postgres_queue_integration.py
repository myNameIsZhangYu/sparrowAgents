import asyncio
import os
import uuid

import pytest

from person_agents.memory.postgres_store import PostgresStore


def postgres_dsn() -> str | None:
    return os.environ.get("PERSON_AGENTS_TEST_POSTGRES_DSN")


pytestmark = pytest.mark.skipif(not postgres_dsn(), reason="PERSON_AGENTS_TEST_POSTGRES_DSN is not set")


async def test_concurrent_claim_and_cancellation() -> None:
    dsn = postgres_dsn()
    assert dsn
    store = PostgresStore(dsn)
    await store.bootstrap()
    run_id = str(uuid.uuid4())
    await store.create_run(run_id, "prompt", "user", "tenant", "session", provenance={})

    results = await asyncio.gather(
        *(store.claim_next_run(f"worker-{worker}", 60) for worker in range(5))
    )
    claimed = [job for job in results if job and job["run_id"] == run_id]
    assert len(claimed) == 1
    assert claimed[0]["attempts"] == 1

    await store.update_run(run_id, "user", "tenant", "canceled", error="Run canceled")
    run = await store.get_run(run_id, "user", "tenant")
    assert run["status"] == "canceled"
