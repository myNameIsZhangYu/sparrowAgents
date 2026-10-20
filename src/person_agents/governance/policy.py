import re
from enum import StrEnum

import structlog

logger = structlog.get_logger()

DANGEROUS_PATTERNS = [
    # 覆盖不可逆文件删除、格式化、断电、强推和 SQL 删除等高危动作。
    r"rm\s+(-\w*r\w*f|-\w*f\w*r|-\w*r\s+-\w*f|-\w*f\s+-\w*r)\s+[/~]",
    r"mkfs",
    r"dd\s+if=/dev/(zero|random)",
    r"\bshutdown\b",
    r"\breboot\b",
    r"git\s+push\s+(-f\b|--force)",
    r"git\s+reset\s+--hard",
    r"drop\s+(database|table)",
    r":\(\)\s*\{.*\};:",
]
UNSAFE_CONTENT_PATTERNS = [
    # 先拦截两类明显 prompt injection 请求；输出侧也会复用这些规则。
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"reveal\s+(the\s+)?system\s+prompt",
]


class Permission(StrEnum):
    READ = "read"
    WRITE = "write"
    SHELL = "shell"
    NETWORK = "network"


class PolicyEngine:
    def __init__(self) -> None:
        # 默认只授读取；写与 shell 必须显式授权。
        self._granted: set[Permission] = {Permission.READ}

    def grant(self, permission: Permission) -> None:
        self._granted.add(permission)

    def revoke(self, permission: Permission) -> None:
        self._granted.discard(permission)

    def check(self, permission: Permission) -> bool:
        return permission in self._granted

    def check_command(self, command: str) -> tuple[bool, str]:
        # 对 shell 命令做黑名单检查，返回原因便于审计。
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"Blocked dangerous pattern: {pattern}"
        return True, ""

    def check_content(self, content: str) -> tuple[bool, str]:
        # 输入与输出统一走内容策略，降低提示词注入风险。
        for pattern in UNSAFE_CONTENT_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                return False, f"Blocked prompt injection pattern: {pattern}"
        return True, ""

    def sanitize_tool_output(self, output: str) -> tuple[bool, str]:
        # 工具输出可能包含外部内容，必须再次过策略。
        allowed, reason = self.check_content(output)
        if allowed:
            return True, output
        return False, reason
