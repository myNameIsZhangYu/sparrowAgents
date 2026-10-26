
from person_agents.graph.task_graph import execute_task_graph


async def run_multi_agent_eval() -> dict[str, object]:
    async def runner(subtask: dict[str, object]) -> dict[str, object]:
        return {"output": f"completed:{subtask['id']}"}

    subtasks = [
        {"id": "task-0", "tool": "none", "depends_on": [], "agent": "executor"},
        {"id": "task-1", "tool": "none", "depends_on": [], "agent": "executor"},
        {"id": "task-2", "tool": "none", "depends_on": ["task-0", "task-1"], "agent": "executor"},
    ]

    results = await execute_task_graph(subtasks, runner)
    completed = {result["subtask_id"] for result in results if result["status"] == "completed"}

    return {
        "parallel_execution": len(results) == 3,
        "dependency_scheduling": "task-2" in completed,
        "delegation": all(task["agent"] == "executor" for task in subtasks),
        "recovery": all(result["error"] is None for result in results),
        "termination": len(results) == len(subtasks),
        "passed": completed == {"task-0", "task-1", "task-2"},
    }
