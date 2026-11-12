# Development Guide

## Setup
```bash
uv sync --all-extras
uv run person-agents version
uv run person-agents doctor
```

## Running
```bash
# Single prompt
uv run person-agents run "Hello"

# Interactive chat
uv run person-agents chat

# Health check
uv run person-agents doctor
```

## Testing
```bash
uv run pytest -q
uv run ruff check src tests
```

## Observability (Langfuse)
```bash
docker compose up -d
# Open http://localhost:3000
```

## Sandbox
```bash
docker build -t person-agents/sandbox:latest .
```

## Super Agent Development

- DAG planning: `src/person_agents/graph/plan_model.py`
- Agent runtime: `src/person_agents/graph/agent_runtime.py`
- HTTP MCP adapter: `src/person_agents/tools/mcp_http.py`
- Task checkpoints/artifacts: `src/person_agents/graph/checkpoint_store.py`
- Skill store: `src/person_agents/skills/skill_store.py`
- Evaluation gate: `src/person_agents/eval/super_agent_metrics.py`

MCP settings:

```bash
PERSON_AGENTS_MCP_SERVER_URL=http://mcp-server:9000
PERSON_AGENTS_MCP_SERVER_TOKEN=optional-bearer-token
PERSON_AGENTS_MCP_TIMEOUT_S=10
```

## Project Structure
Architecture plans are kept in the local working folder and are not part of the public repository.
