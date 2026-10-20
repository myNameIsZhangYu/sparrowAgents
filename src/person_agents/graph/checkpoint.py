from langgraph.checkpoint.sqlite import SqliteSaver

from person_agents.config.settings import settings


def get_checkpointer() -> SqliteSaver:
    import sqlite3
    # CLI/单机路径的同步 checkpointer；check_same_thread 关闭用于受控重用。
    conn = sqlite3.connect(str(settings.sqlite_path), check_same_thread=False)
    return SqliteSaver(conn)
