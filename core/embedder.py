"""
임베딩 처리기 - 다중 제공자 지원 (개선판)

지원 제공자:
- dummy: 해시 기반 deterministic 임베딩 (기본값)
- qwen3: Qwen3 Embeddings (langchain-huggingface / langchain_community)
- openai: OpenAI Embeddings (openai 패키지)
- upstage: (플레이스홀더, 향후)

환경변수:
- EMBEDDING_PROVIDER: dummy (기본), qwen3, openai, upstage
- EMBEDDING_DIM: 임베딩 차원 (기본: 384)
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import List, Optional, Sequence, Union

import numpy as np

logger = logging.getLogger(__name__)

# 기본 벡터 차원 (환경변수로 조절 가능)
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))

# Optional deps 플래그
try:
    # prefer modern langchain-huggingface if available
    from langchain_huggingface import HuggingFaceEmbeddings  # type: ignore
    QWEN3_AVAILABLE = True
except Exception:
    try:
        # fallback
        from langchain_community.embeddings import HuggingFaceEmbeddings  # type: ignore
        QWEN3_AVAILABLE = True
        logger.warning("Using deprecated langchain_community.embeddings. Consider upgrading to langchain-huggingface.")
    except Exception:
        QWEN3_AVAILABLE = False
        logger.warning("langchain-huggingface (or langchain_community) not installed. Qwen3 embeddings not available.")

try:
    from openai import OpenAI  # type: ignore
    OPENAI_AVAILABLE = True
except Exception:
    OPENAI_AVAILABLE = False
    logger.warning("openai not installed. OpenAI embeddings not available.")


# ---------------------------
# 유틸: ndarray로 변환 + L2 정규화
# ---------------------------
def ensure_ndarray_normalized(
    arr: Union[Sequence[float], np.ndarray],
    dim: Optional[int] = None,
    eps: float = 1e-12,
) -> np.ndarray:
    """
    입력을 numpy.ndarray(float32)로 변환하고 L2 정규화합니다.
    - arr: 1-D sequence 또는 ndarray 또는 2-D 배열(여러 벡터)
    - dim: 임베딩 차원(선택). 주어지면 shape 검증함.
    반환: np.ndarray of shape (..., dim) dtype float32, L2-normalized along last axis.
    """
    a = np.asarray(arr, dtype=np.float32)
    if a.ndim == 1:
        a = a.reshape(1, -1)
    if a.ndim != 2:
        raise ValueError(f"Expected 1-D or 2-D array-like, got shape {a.shape}")
    if dim is not None and a.shape[1] != dim:
        raise ValueError(f"Dimension mismatch: expected dim={dim}, got {a.shape[1]}")
    norms = np.linalg.norm(a, axis=1, keepdims=True)
    norms = np.maximum(norms, eps)
    a = a / norms
    return a.astype(np.float32)


def ndarray_to_list_of_lists(a: np.ndarray) -> List[List[float]]:
    """
    numpy array -> List[List[float]] (flattened row-wise).
    Preserves float precision in values; dtype may be float32 but Python floats are float64 when extracted.
    """
    if a.ndim == 1:
        return a.astype(float).tolist()
    return a.astype(float).tolist()


# ---------------------------
# Dummy Embedder (deterministic)
# ---------------------------
def _seed_from_text(text: str) -> int:
    # blake2b으로 안정적 해시를 얻고 처음 8 bytes를 사용해 seed 생성
    h = hashlib.blake2b(text.encode("utf-8"), digest_size=64)
    b = h.digest()
    # 8 bytes -> 64-bit seed, reduce to numpy RNG seed range if needed
    seed = int.from_bytes(b[:8], byteorder="big", signed=False) % (2**63 - 1)
    return int(seed)


def embed_texts_dummy(texts: List[str], dim: int = EMBEDDING_DIM, return_numpy: bool = False) -> Union[List[List[float]], np.ndarray]:
    """
    deterministic dummy embeddings:
    - 각 텍스트를 해시로 시드한 RNG로 uniform(-1,1) 샘플링
    - L2 정규화 보장
    - 기본 반환: List[List[float]] (호환성)
    - return_numpy=True 이면 np.ndarray (N, dim) 반환
    """
    if not texts:
        return np.empty((0, dim), dtype=np.float32) if return_numpy else []

    logger.info(f"[Dummy] Embedding {len(texts)} texts with dim={dim} (deterministic)")

    vectors = np.empty((len(texts), dim), dtype=np.float32)
    for i, t in enumerate(texts):
        seed = _seed_from_text(t)
        rng = np.random.default_rng(seed)
        v = rng.uniform(-1.0, 1.0, size=(dim,)).astype(np.float32)
        # 정규화
        v_norm = np.linalg.norm(v)
        if v_norm > 0:
            v = v / v_norm
        else:
            # 안전 fallback: 작은 랜덤 벡터
            v = rng.normal(size=(dim,)).astype(np.float32)
            v = v / (np.linalg.norm(v) + 1e-12)
        vectors[i] = v

    if return_numpy:
        return vectors
    return ndarray_to_list_of_lists(vectors)


# ---------------------------
# Qwen3 Embedder (HuggingFace via LangChain wrapper)
# ---------------------------
class Qwen3Embedder:
    def __init__(self, model_name: Optional[str] = None, dim: Optional[int] = None):
        if not QWEN3_AVAILABLE:
            raise RuntimeError("Qwen3 embeddings not available. Install langchain-huggingface or langchain-community.")
        self.model_name = model_name or os.getenv(
            "QWEN3_MODEL_NAME",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        self.dim = dim or EMBEDDING_DIM
        logger.info(f"[Qwen3] Initializing model: {self.model_name}")
        try:
            # HuggingFaceEmbeddings 래퍼: 인스턴스화
            # encode_kwargs / model_kwargs는 래퍼에 따라 다름. 실패시 예외 발생.
            self.model = HuggingFaceEmbeddings(
                model_name=self.model_name,
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": False},  # 우리가 직접 정규화 함
            )
            logger.info("[Qwen3] Model loaded")
        except Exception as e:
            logger.exception(f"[Qwen3] Failed to initialize HuggingFaceEmbeddings: {e}")
            raise

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        logger.info(f"[Qwen3] Embedding {len(texts)} texts using {self.model_name}")
        try:
            # langchain wrapper 메서드 이름이 버전에 따라 다를 수 있음:
            if hasattr(self.model, "embed_documents"):
                raw = self.model.embed_documents(texts)
            elif hasattr(self.model, "embed_query"):
                # embed_query는 단문용일 수 있으므로 map 사용
                raw = [self.model.embed_query(t) for t in texts]
            else:
                # fallback: try calling model directly
                raw = self.model.embed_documents(texts)
            arr = ensure_ndarray_normalized(np.asarray(raw, dtype=np.float32), dim=self.dim)
            return ndarray_to_list_of_lists(arr)
        except Exception as e:
            logger.exception(f"[Qwen3] Embedding failed: {e}")
            raise


# ---------------------------
# OpenAI Embedder
# ---------------------------
class OpenAIEmbedder:
    def __init__(self, api_key: Optional[str] = None, model: str = "text-embedding-3-small", dim: Optional[int] = None):
        if not OPENAI_AVAILABLE:
            raise RuntimeError("openai package not installed. pip install openai")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set (env or api_key argument)")
        self.model = model
        self.dim = dim or EMBEDDING_DIM
        try:
            self.client = OpenAI(api_key=self.api_key)
        except Exception as e:
            logger.exception(f"[OpenAI] Failed to initialize client: {e}")
            raise
        logger.info(f"[OpenAI] Initialized embedder with model={self.model}")

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        logger.info(f"[OpenAI] Embedding {len(texts)} texts with model {self.model}")
        try:
            # OpenAI python client의 반환 형식에 따라 다름
            resp = self.client.embeddings.create(input=texts, model=self.model)
            # resp.data는 리스트, 각 항목에 .embedding 존재
            vectors = [np.asarray(item.embedding, dtype=np.float32) for item in resp.data]
            arr = ensure_ndarray_normalized(np.vstack(vectors), dim=self.dim)
            return ndarray_to_list_of_lists(arr)
        except Exception as e:
            logger.exception(f"[OpenAI] Embedding failed: {e}")
            raise


# ---------------------------
# 통합 임베더 팩토리 & API
# ---------------------------
class _BaseEmbedderWrapper:
    _provider: str = "unknown"

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError()


_embedder_instance: Optional[_BaseEmbedderWrapper] = None
_current_provider: Optional[str] = None


def get_embedder(provider: Optional[str] = None) -> _BaseEmbedderWrapper:
    """
    provider: "dummy" | "qwen3" | "openai" | ...
    - 환경변수 EMBEDDING_PROVIDER가 기본값을 가짐.
    - provider가 변경되면 내부 인스턴스를 재생성합니다.
    """
    global _embedder_instance, _current_provider

    provider = provider or os.getenv("EMBEDDING_PROVIDER", "dummy")
    provider = provider.lower()

    if _embedder_instance is not None and _current_provider == provider:
        return _embedder_instance

    logger.info(f"[embedder] Initializing provider={provider}")

    if provider == "dummy":
        class DummyWrapper(_BaseEmbedderWrapper):
            _provider = "dummy"
            def embed_texts(self, texts: List[str]) -> List[List[float]]:
                return embed_texts_dummy(texts, dim=EMBEDDING_DIM, return_numpy=False)
        _embedder_instance = DummyWrapper()

    elif provider == "qwen3":
        # instantiate Qwen3Embedder and wrap
        embedder = Qwen3Embedder(dim=EMBEDDING_DIM)
        class Wrapper(_BaseEmbedderWrapper):
            _provider = "qwen3"
            def embed_texts(self, texts: List[str]) -> List[List[float]]:
                return embedder.embed_texts(texts)
        _embedder_instance = Wrapper()

    elif provider == "openai":
        embedder = OpenAIEmbedder(dim=EMBEDDING_DIM)
        class Wrapper(_BaseEmbedderWrapper):
            _provider = "openai"
            def embed_texts(self, texts: List[str]) -> List[List[float]]:
                return embedder.embed_texts(texts)
        _embedder_instance = Wrapper()

    else:
        raise ValueError(f"Unknown embedding provider: {provider}")

    _current_provider = provider
    return _embedder_instance


def embed_texts(texts: List[str], provider: Optional[str] = None) -> List[List[float]]:
    """
    편의 함수: provider 지정 가능. 기본은 환경변수 EMBEDDING_PROVIDER.
    항상 List[List[float]] 반환 (빈 리스트 가능).
    """
    embedder = get_embedder(provider)
    return embedder.embed_texts(texts)


def embed_single_text(text: str, provider: Optional[str] = None) -> List[float]:
    res = embed_texts([text], provider=provider)
    if not res:
        return []
    return res[0]


# ---------------------------
# Deprecated 호환성 레이어
# ---------------------------
class EmbedderInterface:
    def __init__(self, provider: str = "dummy", api_key: Optional[str] = None):
        logger.warning("EmbedderInterface is deprecated. Use get_embedder() or embed_texts() instead.")
        self.provider = provider
        self.api_key = api_key
        self._embedder = get_embedder(provider)

    def embed(self, texts: List[str]) -> List[List[float]]:
        return self._embedder.embed_texts(texts)


# ---------------------------
# 간단한 자체 테스트 (모듈로 실행할 때)
# ---------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("EMBEDDING_DIM:", EMBEDDING_DIM)
    sample = ["hello world", "hello", "다음 문장입니다"]
    print("Dummy embeddings (lists):")
    emb = embed_texts(sample, provider="dummy")
    for e in emb:
        print(len(e), type(e[0]), e[:3])

    print("\nAs numpy (normalized):")
    np_emb = ensure_ndarray_normalized(emb, dim=EMBEDDING_DIM)
    print(np_emb.shape, np.max(np.linalg.norm(np_emb, axis=1)))
