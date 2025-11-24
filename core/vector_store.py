# core/vector_store.py
import logging
import json
from pathlib import Path
from typing import List, Dict, Any
import numpy as np
import faiss

logger = logging.getLogger(__name__)

VECTOR_STORE_DIR = Path("data/vector_store")
INDEX_FILE = VECTOR_STORE_DIR / "faiss.index"
METADATA_FILE = VECTOR_STORE_DIR / "metadata.jsonl"


class FAISSVectorStore:
    """
    FAISS 기반 벡터 스토어
    - IndexFlatIP 사용, 벡터는 항상 L2 정규화 (cosine similarity)
    - Metadata는 METADATA_FILE에 JSONL 형식으로 저장
    """

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.index = faiss.IndexFlatIP(self.dim)
        self.vector_count = 0
        VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"FAISSVectorStore initialized with dimension {dim}")

    def load_index_if_exists(self):
        """기존 index 파일이 있으면 로드하고 vector_count 반영"""
        if INDEX_FILE.exists():
            try:
                self.index = faiss.read_index(str(INDEX_FILE))
                self.vector_count = self.index.ntotal
                logger.info(f"FAISS index loaded from {INDEX_FILE} (ntotal={self.vector_count})")
            except Exception as e:
                logger.warning(f"Failed to load FAISS index: {e}. Creating new index.")
                self.index = faiss.IndexFlatIP(self.dim)
                self.vector_count = 0

    def save_index(self):
        try:
            faiss.write_index(self.index, str(INDEX_FILE))
        except Exception as e:
            logger.exception(f"Failed to save FAISS index: {e}")
            raise

    # -------------------------
    # Metadata helpers
    # -------------------------
    def _append_metadata(self, metadatas: List[Dict[str, Any]]):
        with open(METADATA_FILE, "a", encoding="utf-8") as f:
            for i, meta in enumerate(metadatas):
                vector_id = self.vector_count + i
                meta_with_id = {"vector_id": vector_id, **meta}
                f.write(json.dumps(meta_with_id, ensure_ascii=False) + "\n")

    def _load_metadata_map(self) -> Dict[int, Dict[str, Any]]:
        if not METADATA_FILE.exists():
            return {}
        meta_map: Dict[int, Dict[str, Any]] = {}
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    vector_id = int(data.pop("vector_id"))
                    meta_map[vector_id] = data
                except Exception:
                    logger.exception("Failed to parse metadata line; skipping.")
                    continue
        return meta_map

    # -------------------------
    # Add vectors
    # -------------------------
    def add_vectors(self, vectors: List[List[float]], metadatas: List[Dict[str, Any]]):
        if len(vectors) != len(metadatas):
            raise ValueError("vectors and metadatas must match length")
        if not vectors:
            return

        vectors_np = np.asarray(vectors, dtype=np.float32)
        if vectors_np.ndim != 2 or vectors_np.shape[1] != self.dim:
            raise ValueError(f"vectors must be shape (N, {self.dim}), got {vectors_np.shape}")

        # L2 정규화
        faiss.normalize_L2(vectors_np)

        # index에 추가
        self.index.add(vectors_np)

        # metadata append
        self._append_metadata(metadatas)

        # vector_count 업데이트 및 index 저장
        self.vector_count += len(vectors)
        self.save_index()
        logger.info(f"Added {len(vectors)} vectors (total now {self.vector_count})")

    # -------------------------
    # Search
    # -------------------------
    def search(self, query_vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        if not self.index or self.index.ntotal == 0:
            return []

        query_np = np.asarray(query_vector, dtype=np.float32).reshape(1, -1)
        if query_np.shape[1] != self.dim:
            raise ValueError(f"query_vector must have dimension {self.dim}, got {query_np.shape[1]}")

        faiss.normalize_L2(query_np)
        distances, indices = self.index.search(query_np, top_k)

        meta_map = self._load_metadata_map()
        results: List[Dict[str, Any]] = []

        for score, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue
            meta = meta_map.get(int(idx), {})
            results.append({"score": float(score), "vector_id": int(idx), **meta})

        return results

    # -------------------------
    # Reset
    # -------------------------
    def reset(self):
        if INDEX_FILE.exists():
            INDEX_FILE.unlink()
        if METADATA_FILE.exists():
            METADATA_FILE.unlink()
        self.index = faiss.IndexFlatIP(self.dim)
        self.vector_count = 0
        logger.info("Vector store reset: index and metadata removed")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "dimension": self.dim,
            "total_vectors": self.index.ntotal if self.index else 0
        }


# -------------------------
# Singleton accessor
# -------------------------
_vector_store_instance = None

def get_vector_store(dim: int = 384):
    global _vector_store_instance
    if _vector_store_instance is None:
        _vector_store_instance = FAISSVectorStore(dim)
        _vector_store_instance.load_index_if_exists()
    return _vector_store_instance
