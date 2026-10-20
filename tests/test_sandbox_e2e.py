import os

import pytest

from person_agents.tools.sandbox import DockerSandbox

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SANDBOX_E2E") != "true",
    reason="RUN_SANDBOX_E2E is not enabled",
)


async def test_sandbox_executes_isolated_command() -> None:
    sandbox = DockerSandbox()
    result = await sandbox.execute("printf controlled-output")

    assert result.exit_code == 0
    assert result.stdout == "controlled-output"
    assert result.stderr == ""


async def test_sandbox_returns_command_exit_code() -> None:
    sandbox = DockerSandbox()
    result = await sandbox.execute("exit 7")

    assert result.exit_code == 7
