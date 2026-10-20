from person_agents.eval.runner import EvalCase

BASELINE_CASES = [
    # 最小发布前基线：寒暄、推理、记忆召回。
    EvalCase(name="simple_greeting", input="Hello", expected_contains="hello", category="chat"),
    EvalCase(name="math", input="What is 2+2?", expected_contains="4", category="reasoning"),
    EvalCase(name="memory_recall", input="What do I prefer?", expected_contains="prefer", category="memory"),
]
