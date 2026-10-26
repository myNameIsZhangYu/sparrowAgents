from person_agents.graph.task_graph import execute_task_graph


async def test_execute_task_graph_retries_only_failed_step_by_id():
    executed = []

    async def runner(task):
        executed.append(task["id"])
        if task["id"] == "task-1":
            raise RuntimeError("transient")
        return {"output": f"ok:{task['id']}"}

    tasks = [
        {"id": "task-0", "tool": "none", "depends_on": [], "attempt": 0},
        {"id": "task-1", "tool": "none", "depends_on": ["task-0"], "attempt": 0},
        {"id": "task-2", "tool": "none", "depends_on": ["task-1"], "attempt": 0},
    ]
    first = await execute_task_graph(tasks, runner)
    assert {result["subtask_id"] for result in first} == {"task-0", "task-1", "task-2"}
    skipped = next(result for result in first if result["subtask_id"] == "task-2")
    assert skipped["status"] == "skipped"

    retries = [{"id": "task-1", "tool": "none", "depends_on": ["task-0"], "attempt": 1}]
    second = await execute_task_graph(retries, runner)
    assert [result["subtask_id"] for result in second] == ["task-1"]
    assert executed == ["task-0", "task-1", "task-1"]
