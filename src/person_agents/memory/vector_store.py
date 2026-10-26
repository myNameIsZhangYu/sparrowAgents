import uuid
from pathlib import Path
from typing import Any

import structlog

from person_agents.config.settings import settings

logger = structlog.get_logger()


class VectorStore:
    def __init__(self, persist_dir: Path | None = None, model_cache_path: Path | None = None):
        # Chroma 数据持久化在租户共享目录，检索时再按 owner/tenant 过滤。
        self.persist_dir = persist_dir or settings.chroma_path
        self.model_cache_path = model_cache_path or settings.chroma_model_cache_path
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._client: Any = None
        self._collection: Any = None

    def _ensure_client(self) -> None:
        if self._client is None:
            # 延迟初始化，避免导入模块时创建向量库。
            import chromadb
            from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

            ONNXMiniLM_L6_V2.DOWNLOAD_PATH = self.model_cache_path
            self._client = chromadb.PersistentClient(path=str(self.persist_dir))
            try:
                self._collection = self._client.get_or_create_collection(
                    "memories",
                    embedding_function=ONNXMiniLM_L6_V2(),
                )
            except ValueError:
                # 旧集合使用默认 embedding；不能因为升级而拒绝读取历史记忆。
                self._collection = self._client.get_or_create_collection("memories")

    def _migrate_local_metadata(self) -> None:
        # 早期记录缺少隔离字段；这些记录只允许归属默认本地用户。
        rows = self._collection.get(include=["metadatas"])
        legacy_ids = [
            record_id
            for record_id, metadata in zip(rows.get("ids", []), rows.get("metadatas", []))
            if "owner_user_id" not in metadata or "tenant_id" not in metadata
        ]
        if legacy_ids:
            self._collection.update(ids=legacy_ids, metadatas=[{"owner_user_id": "local", "tenant_id": "default"}] * len(legacy_ids))

    def add(self, text: str, metadata: dict[str, Any] | None = None, owner_user_id: str = "local", tenant_id: str = "default") -> str:
        self._ensure_client()
        doc_id = str(uuid.uuid4())
        # 将租户与用户写入 metadata，后续查询用同一隔离条件。
        scoped_metadata = {**(metadata or {}), "owner_user_id": owner_user_id, "tenant_id": tenant_id}
        self._collection.add(documents=[text], ids=[doc_id], metadatas=[scoped_metadata])
        return doc_id

    def query(self, text: str, n_results: int = 5, owner_user_id: str = "local", tenant_id: str = "default") -> list[dict[str, Any]]:
        self._ensure_client()
        if not isinstance(text, str) or self._collection.count() == 0:
            return []
        try:
            self._migrate_local_metadata()
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("vector_metadata_migration_failed", error=str(exc))
        # 使用 $and 确保两个隔离字段必须同时命中。
        where = {"$and": [{"owner_user_id": {"$eq": owner_user_id}}, {"tenant_id": {"$eq": tenant_id}}]}
        results = self._collection.query(query_texts=[text], n_results=n_results, where=where)
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        return [{"text": d, "metadata": m, "distance": dist} for d, m, dist in zip(docs, metas, distances)]

    def count(self) -> int:
        self._ensure_client()
        # 供健康检查或评估统计使用。
        return self._collection.count()
