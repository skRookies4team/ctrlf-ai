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
    def __init__(self, dim: int = 384):
        self.dim = dim
        self.index = None
        self.vector_count = 0
        VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
        self.init_index()
        logger.info(f"FAISSVectorStore initialized with dimension {dim}")

    def init_index(self):
        if INDEX_FILE.exists():
            self.load_index()
        else:
            self.index = faiss.IndexFlatL2(self.dim)
            self.vector_count = 0

    def add_vectors(self, vectors: List[List[float]], metadatas: List[Dict[str, Any]]):
        if len(vectors) != len(metadatas):
            raise ValueError("vectors and metadatas must match length")
        if not vectors:
            return
        vectors_np = np.array(vectors, dtype=np.float32)
        self.index.add(vectors_np)
        with open(METADATA_FILE, "a", encoding="utf-8") as f:
            for i, meta in enumerate(metadatas):
                vector_id = self.vector_count + i
                meta_with_id = {"vector_id": vector_id, **meta}
                f.write(json.dumps(meta_with_id, ensure_ascii=False) + "\n")
        self.vector_count += len(vectors)
        self.save_index()

    def search(self, query_vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        if self.index.ntotal == 0:
            return []
        query_np = np.array([query_vector], dtype=np.float32)
        distances, indices = self.index.search(query_np, top_k)
        meta_map = self._load_metadata_map()
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue
            meta = meta_map.get(int(idx), {})
            results.append({"score": float(dist), "vector_id": int(idx), **meta})
        return results

    def _load_metadata_map(self) -> Dict[int, Dict[str, Any]]:
        if not METADATA_FILE.exists():
            return {}
        meta_map = {}
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    vector_id = data.pop("vector_id")
                    meta_map[vector_id] = data
                except:
                    continue
        return meta_map

    def save_index(self):
        faiss.write_index(self.index, str(INDEX_FILE))

    def load_index(self):
        try:
            self.index = faiss.read_index(str(INDEX_FILE))
            self.vector_count = self.index.ntotal
        except:
            self.index = faiss.IndexFlatL2(self.dim)
            self.vector_count = 0

    def reset(self):
        if INDEX_FILE.exists():
            INDEX_FILE.unlink()
        if METADATA_FILE.exists():
            METADATA_FILE.unlink()
        self.init_index()
        logger.info("Vector store reset")

    def get_stats(self):
        return {
            "dimension": self.dim,
            "total_vectors": self.index.ntotal if self.index else 0
        }

_vector_store_instance = None

def get_vector_store(dim: int = 384):
    global _vector_store_instance
    if _vector_store_instance is None:
        _vector_store_instance = FAISSVectorStore(dim)
    return _vector_store_instance
