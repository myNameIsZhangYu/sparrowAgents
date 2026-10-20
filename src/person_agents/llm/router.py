from enum import StrEnum


class TaskComplexity(StrEnum):
    # 目前区分简单任务和复杂任务，为后续模型分层预留。
    SIMPLE = "simple"
    COMPLEX = "complex"


def route_task(text: str) -> TaskComplexity:
    # 常见短语 + 短文本走简单模型/轻路径；其余按复杂任务处理。
    simple_keywords = {"hi", "hello", "thanks", "what time", "list", "read", "summarize"}
    lower = text.lower().strip()
    if any(lower.startswith(k) for k in simple_keywords) and len(lower) < 100:
        return TaskComplexity.SIMPLE
    return TaskComplexity.COMPLEX
