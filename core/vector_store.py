"""
벡터 스토어 - FAISS Local Index 기반 Vector DB
"""
import logging
import json
from pathlib import Path
from typing import List, Dict, Any
import numpy as np
import faiss

logger = logging.getLogger(__name__)

# Vector Store 디렉토리
VECTOR_STORE_DIR = Path("data/vector_store")
INDEX_FILE = VECTOR_STORE_DIR / "faiss.index"
METADATA_FILE = VECTOR_STORE_DIR / "metadata.jsonl"


class FAISSVectorStore:
    """FAISS 기반 벡터 스토어"""

    def __init__(self, dim: int = 384):
        """
        Args:
            dim: 벡터 차원 (기본: 384)
        """
        self.dim = dim
        self.index = None
        self.vector_count = 0

        # 디렉토리 생성
        VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)

        # Index 초기화
        self.init_index()

        logger.info(f"FAISSVectorStore initialized with dimension {dim}")

    def init_index(self):
        """FAISS Index 초기화 (없으면 새로 생성, 있으면 로드)"""
        if INDEX_FILE.exists():
            logger.info(f"Loading existing FAISS index from {INDEX_FILE}")
            self.load_index()
        else:
            logger.info(f"Creating new FAISS index with dimension {self.dim}")
            # IndexFlatL2: L2 거리 기반 완전 탐색 인덱스
            self.index = faiss.IndexFlatL2(self.dim)
            self.vector_count = 0

    def add_vectors(self, vectors: List[List[float]], metadatas: List[Dict[str, Any]]):
        """
        벡터와 메타데이터 추가

        Args:
            vectors: 임베딩 벡터 리스트
            metadatas: 메타데이터 리스트 (각 벡터에 대응)
        """
        if len(vectors) != len(metadatas):
            raise ValueError("vectors and metadatas must have the same length")

        if not vectors:
            logger.warning("No vectors to add")
            return

        logger.info(f"Adding {len(vectors)} vectors to FAISS index")

        # List[List[float]] → numpy array (float32)
        vectors_np = np.array(vectors, dtype=np.float32)

        # FAISS에 벡터 추가
        self.index.add(vectors_np)

        # 메타데이터 저장 (JSONL append)
        # vector_id = 현재 인덱스의 글로벌 위치
        with open(METADATA_FILE, "a", encoding="utf-8") as f:
            for i, metadata in enumerate(metadatas):
                vector_id = self.vector_count + i
                metadata_with_id = {
                    "vector_id": vector_id,
                    **metadata
                }
                f.write(json.dumps(metadata_with_id, ensure_ascii=False) + "\n")

        # 벡터 카운트 업데이트
        self.vector_count += len(vectors)

        # Index 저장
        self.save_index()

        logger.info(f"Added {len(vectors)} vectors. Total vectors: {self.vector_count}")

    def search(self, query_vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """
        벡터 유사도 검색

        Args:
            query_vector: 쿼리 임베딩 벡터
            top_k: 반환할 결과 개수

        Returns:
            List[Dict[str, Any]]: 검색 결과
                - score: 유사도 점수 (L2 거리, 작을수록 유사)
                - vector_id: 벡터 ID
                - metadata: 해당 벡터의 메타데이터
        """
        if self.index.ntotal == 0:
            logger.warning("Index is empty, no results to return")
            return []

        logger.info(f"Searching for top {top_k} similar vectors")

        # 쿼리 벡터를 numpy array로 변환
        query_np = np.array([query_vector], dtype=np.float32)

        # FAISS 검색 (distances: L2 거리, indices: 벡터 ID)
        distances, indices = self.index.search(query_np, top_k)

        # 메타데이터 로드
        metadata_map = self._load_metadata_map()

        # 결과 구성
        results = []
        for i, (dist, idx) in enumerate(zip(distances[0], indices[0])):
            # idx가 -1이면 결과 없음 (top_k가 전체 벡터 수보다 큰 경우)
            if idx == -1:
                continue

            vector_id = int(idx)
            metadata = metadata_map.get(vector_id, {})

            result = {
                "score": float(dist),  # L2 거리 (작을수록 유사)
                "vector_id": vector_id,
                **metadata
            }
            results.append(result)

        logger.info(f"Search returned {len(results)} results")
        return results

    def _load_metadata_map(self) -> Dict[int, Dict[str, Any]]:
        """메타데이터 파일을 로드하여 vector_id → metadata 맵 생성"""
        if not METADATA_FILE.exists():
            return {}

        metadata_map = {}

        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    vector_id = data.pop("vector_id")
                    metadata_map[vector_id] = data
                except Exception as e:
                    logger.warning(f"Failed to parse metadata line: {e}")

        return metadata_map

    def save_index(self):
        """FAISS Index를 디스크에 저장"""
        try:
            faiss.write_index(self.index, str(INDEX_FILE))
            logger.info(f"FAISS index saved to {INDEX_FILE}")
        except Exception as e:
            logger.error(f"Failed to save FAISS index: {e}", exc_info=True)

    def load_index(self):
        """FAISS Index를 디스크에서 로드"""
        try:
            self.index = faiss.read_index(str(INDEX_FILE))
            self.vector_count = self.index.ntotal
            logger.info(f"FAISS index loaded from {INDEX_FILE}. Total vectors: {self.vector_count}")
        except Exception as e:
            logger.error(f"Failed to load FAISS index: {e}", exc_info=True)
            # 로드 실패 시 새로 생성
            self.index = faiss.IndexFlatL2(self.dim)
            self.vector_count = 0

    def get_stats(self) -> Dict[str, Any]:
        """벡터 스토어 통계 정보 반환"""
        metadata_count = 0
        if METADATA_FILE.exists():
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                metadata_count = sum(1 for line in f if line.strip())

        stats = {
            "dimension": self.dim,
            "total_vectors": self.index.ntotal if self.index else 0,
            "vector_count": self.vector_count,
            "metadata_count": metadata_count,
            "index_file_exists": INDEX_FILE.exists(),
            "metadata_file_exists": METADATA_FILE.exists(),
            "index_file_path": str(INDEX_FILE),
            "metadata_file_path": str(METADATA_FILE)
        }

        logger.info(f"VectorStore stats: {stats}")
        return stats


# 전역 인스턴스 (싱글톤 패턴)
_vector_store_instance = None


def get_vector_store(dim: int = 384) -> FAISSVectorStore:
    """
    VectorStore 싱글톤 인스턴스 반환

    Args:
        dim: 벡터 차원

    Returns:
        FAISSVectorStore: 벡터 스토어 인스턴스
    """
    global _vector_store_instance

    if _vector_store_instance is None:
        _vector_store_instance = FAISSVectorStore(dim=dim)

    return _vector_store_instance
