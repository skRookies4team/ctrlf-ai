"""
임베딩 처리기 - 다중 제공자 지원

지원 제공자:
- dummy: 해시 기반 deterministic 임베딩 (기본값)
- qwen3: Qwen3 Embeddings (langchain_qwen3)
- openai: OpenAI Embeddings (향후)
- upstage: Upstage Embeddings (향후)

환경변수:
- EMBEDDING_PROVIDER: dummy (기본), qwen3, openai, upstage
- EMBEDDING_DIM: 임베딩 차원 (기본: 384)
"""
import logging
import hashlib
import numpy as np
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

# 기본 벡터 차원
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))

# ========================================
# 선택적 의존성 체크
# ========================================

try:
    from langchain_huggingface import HuggingFaceEmbeddings
    QWEN3_AVAILABLE = True
except ImportError:
    try:
        from langchain_community.embeddings import HuggingFaceEmbeddings
        QWEN3_AVAILABLE = True
        logger.warning("Using deprecated langchain_community.embeddings. Consider upgrading to langchain-huggingface.")
    except ImportError:
        QWEN3_AVAILABLE = False
        logger.warning("langchain-huggingface not installed. Qwen3 embeddings not available.")

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("openai not installed. OpenAI embeddings not available.")


# ========================================
# Dummy Embedder (안정화)
# ========================================

def embed_texts_dummy(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []

    logger.info(f"[Dummy] Embedding {len(texts)} texts with dimension {EMBEDDING_DIM}")

    vectors = []

    for text in texts:
        # blake2b 해시 -> deterministic 32bit seed
        hash_obj = hashlib.blake2b(text.encode('utf-8'), digest_size=64)
        hash_bytes = hash_obj.digest()
        seed = int.from_bytes(hash_bytes[:4], byteorder='big') % (2**32)
        rng = np.random.RandomState(seed)

        # uniform 분포에서 샘플링 후 L2 정규화
        vector = rng.uniform(-1, 1, EMBEDDING_DIM).astype(np.float32)
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm

        vectors.append(vector.tolist())

    logger.info(f"[Dummy] Successfully embedded {len(vectors)} texts")
    return vectors


# ========================================
# Qwen3 Embedder
# ========================================

class Qwen3Embedder:
    def __init__(self, model_name: Optional[str] = None):
        if not QWEN3_AVAILABLE:
            raise RuntimeError("langchain_community not installed. Run: pip install langchain-community sentence-transformers")

        if model_name is None:
            model_name = os.getenv(
                "QWEN3_MODEL_NAME",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            )

        logger.info(f"[Qwen3] Initializing model: {model_name}")

        try:
            self.model = HuggingFaceEmbeddings(
                model_name=model_name,
                model_kwargs={'device': 'cpu'},
                encode_kwargs={'normalize_embeddings': True}
            )
            logger.info(f"[Qwen3] Model loaded successfully")
        except Exception as e:
            logger.error(f"[Qwen3] Failed to load model: {e}")
            raise

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        logger.info(f"[Qwen3] Embedding {len(texts)} texts")
        try:
            vectors = self.model.embed_documents(texts)
            logger.info(f"[Qwen3] Successfully embedded {len(vectors)} texts")
            return vectors
        except Exception as e:
            logger.error(f"[Qwen3] Embedding failed: {e}")
            raise


# ========================================
# OpenAI Embedder
# ========================================

class OpenAIEmbedder:
    def __init__(self, api_key: Optional[str] = None, model: str = "text-embedding-3-small"):
        if not OPENAI_AVAILABLE:
            raise RuntimeError("openai not installed. Run: pip install openai")

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set")

        self.model = model
        self.client = OpenAI(api_key=self.api_key)
        logger.info(f"[OpenAI] Initialized with model: {model}")

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        logger.info(f"[OpenAI] Embedding {len(texts)} texts")
        try:
            response = self.client.embeddings.create(
                input=texts,
                model=self.model
            )
            vectors = [item.embedding for item in response.data]
            logger.info(f"[OpenAI] Successfully embedded {len(vectors)} texts")
            return vectors
        except Exception as e:
            logger.error(f"[OpenAI] Embedding failed: {e}")
            raise


# ========================================
# 통합 임베딩 함수
# ========================================

_embedder_instance = None

def get_embedder(provider: Optional[str] = None):
    global _embedder_instance

    if provider is None:
        provider = os.getenv("EMBEDDING_PROVIDER", "dummy")

    if _embedder_instance is not None and hasattr(_embedder_instance, '_provider'):
        if _embedder_instance._provider == provider:
            return _embedder_instance

    logger.info(f"Initializing embedder: {provider}")

    if provider == "dummy":
        class DummyWrapper:
            _provider = "dummy"
            def embed_texts(self, texts):
                return embed_texts_dummy(texts)
        _embedder_instance = DummyWrapper()

    elif provider == "qwen3":
        embedder = Qwen3Embedder()
        embedder._provider = "qwen3"
        _embedder_instance = embedder

    elif provider == "openai":
        embedder = OpenAIEmbedder()
        embedder._provider = "openai"
        _embedder_instance = embedder

    else:
        raise ValueError(f"Unknown embedding provider: {provider}")

    return _embedder_instance


def embed_texts(texts: List[str]) -> List[List[float]]:
    embedder = get_embedder()
    return embedder.embed_texts(texts)


def embed_single_text(text: str) -> List[float]:
    return embed_texts([text])[0]


# ========================================
# 하위 호환성 (Deprecated)
# ========================================

class EmbedderInterface:
    def __init__(self, provider: str = "dummy", api_key: str = None):
        logger.warning("EmbedderInterface is deprecated. Use get_embedder() instead.")
        self.provider = provider
        self.api_key = api_key
        self._embedder = get_embedder(provider)

    def embed(self, texts: List[str]) -> List[List[float]]:
        return self._embedder.embed_texts(texts)
