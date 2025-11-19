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

# Qwen3 Embeddings (HuggingFace)
try:
    from langchain_huggingface import HuggingFaceEmbeddings
    QWEN3_AVAILABLE = True
except ImportError:
    try:
        # Fallback to deprecated import
        from langchain_community.embeddings import HuggingFaceEmbeddings
        QWEN3_AVAILABLE = True
        logger.warning("Using deprecated langchain_community.embeddings. Consider upgrading to langchain-huggingface.")
    except ImportError:
        QWEN3_AVAILABLE = False
        logger.warning("langchain-huggingface not installed. Qwen3 embeddings not available.")

# OpenAI Embeddings
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("openai not installed. OpenAI embeddings not available.")


# ========================================
# Dummy Embedder (기본값)
# ========================================

def embed_texts_dummy(texts: List[str]) -> List[List[float]]:
    """
    Dummy embedding function for local testing.
    Returns deterministic pseudo-random vectors based on text hash.

    텍스트 내용이 같으면 항상 동일한 벡터를 반환합니다.
    FAISS에서 실제 insert/lookup이 가능하도록 숫자 배열을 생성합니다.

    Args:
        texts: 텍스트 리스트

    Returns:
        List[List[float]]: 임베딩 벡터 리스트 (차원: 384)
    """
    if not texts:
        return []

    logger.info(f"[Dummy] Embedding {len(texts)} texts with dimension {EMBEDDING_DIM}")

    vectors = []

    for text in texts:
        # blake2b 해시를 사용하여 deterministic vector 생성
        hash_obj = hashlib.blake2b(text.encode('utf-8'), digest_size=64)
        hash_bytes = hash_obj.digest()

        # 해시를 시드로 사용하여 numpy random generator 생성
        seed = int.from_bytes(hash_bytes[:4], byteorder='big')
        rng = np.random.RandomState(seed)

        # 정규분포를 따르는 벡터 생성
        vector = rng.randn(EMBEDDING_DIM).astype(np.float32)

        # L2 정규화 (벡터 크기를 1로 만듦)
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm

        vectors.append(vector.tolist())

    logger.info(f"[Dummy] Successfully embedded {len(vectors)} texts")
    return vectors


# ========================================
# Qwen3 Embedder (세희 코드 기반)
# ========================================

class Qwen3Embedder:
    """
    Qwen3 임베딩 모델

    ⚠️ 세희 코드에서 영감을 받음 (langflow_세희/app.py)

    모델: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
    또는 Qwen 계열 모델
    """

    def __init__(self, model_name: Optional[str] = None):
        """
        Args:
            model_name: HuggingFace 모델명 (기본: paraphrase-multilingual-MiniLM-L12-v2)
        """
        if not QWEN3_AVAILABLE:
            raise RuntimeError("langchain_community not installed. Run: pip install langchain-community sentence-transformers")

        # 기본 모델: 다국어 지원 모델
        if model_name is None:
            model_name = os.getenv(
                "QWEN3_MODEL_NAME",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            )

        logger.info(f"[Qwen3] Initializing model: {model_name}")

        try:
            self.model = HuggingFaceEmbeddings(
                model_name=model_name,
                model_kwargs={'device': 'cpu'},  # GPU 없어도 작동
                encode_kwargs={'normalize_embeddings': True}  # L2 정규화
            )
            logger.info(f"[Qwen3] Model loaded successfully")
        except Exception as e:
            logger.error(f"[Qwen3] Failed to load model: {e}")
            raise

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        텍스트 임베딩 생성

        Args:
            texts: 텍스트 리스트

        Returns:
            List[List[float]]: 임베딩 벡터 리스트
        """
        if not texts:
            return []

        logger.info(f"[Qwen3] Embedding {len(texts)} texts")

        try:
            # HuggingFace 모델로 임베딩 생성
            vectors = self.model.embed_documents(texts)
            logger.info(f"[Qwen3] Successfully embedded {len(vectors)} texts")
            return vectors
        except Exception as e:
            logger.error(f"[Qwen3] Embedding failed: {e}")
            raise


# ========================================
# OpenAI Embedder (향후 지원)
# ========================================

class OpenAIEmbedder:
    """OpenAI Embeddings API"""

    def __init__(self, api_key: Optional[str] = None, model: str = "text-embedding-3-small"):
        """
        Args:
            api_key: OpenAI API 키
            model: 모델명 (text-embedding-3-small, text-embedding-3-large)
        """
        if not OPENAI_AVAILABLE:
            raise RuntimeError("openai not installed. Run: pip install openai")

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set")

        self.model = model
        self.client = OpenAI(api_key=self.api_key)
        logger.info(f"[OpenAI] Initialized with model: {model}")

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """텍스트 임베딩 생성"""
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
# 통합 임베딩 함수 (환경변수 기반 자동 선택)
# ========================================

# 전역 임베더 인스턴스 (싱글톤)
_embedder_instance = None


def get_embedder(provider: Optional[str] = None):
    """
    임베더 인스턴스 가져오기 (싱글톤)

    Args:
        provider: 제공자 (dummy, qwen3, openai) - None이면 환경변수 사용

    Returns:
        임베더 인스턴스
    """
    global _embedder_instance

    if provider is None:
        provider = os.getenv("EMBEDDING_PROVIDER", "dummy")

    # 이미 같은 제공자로 초기화된 경우 재사용
    if _embedder_instance is not None and hasattr(_embedder_instance, '_provider'):
        if _embedder_instance._provider == provider:
            return _embedder_instance

    logger.info(f"Initializing embedder: {provider}")

    if provider == "dummy":
        # Dummy는 클래스가 아니라 함수이므로 wrapper 생성
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
    """
    텍스트 임베딩 (환경변수에 따라 자동 선택)

    환경변수:
    - EMBEDDING_PROVIDER: dummy (기본), qwen3, openai

    Args:
        texts: 텍스트 리스트

    Returns:
        List[List[float]]: 임베딩 벡터 리스트
    """
    embedder = get_embedder()
    return embedder.embed_texts(texts)


def embed_single_text(text: str) -> List[float]:
    """
    단일 텍스트 임베딩 (검색 쿼리용)

    Args:
        text: 텍스트

    Returns:
        List[float]: 임베딩 벡터
    """
    return embed_texts([text])[0]


# ========================================
# 하위 호환성 (Deprecated)
# ========================================

class EmbedderInterface:
    """
    임베딩 제공자 인터페이스 (하위 호환성)

    ⚠️ Deprecated: get_embedder() 사용을 권장
    """

    def __init__(self, provider: str = "dummy", api_key: str = None):
        """
        Args:
            provider: "dummy" | "qwen3" | "openai"
            api_key: API 키
        """
        logger.warning("EmbedderInterface is deprecated. Use get_embedder() instead.")
        self.provider = provider
        self.api_key = api_key
        self._embedder = get_embedder(provider)

    def embed(self, texts: List[str]) -> List[List[float]]:
        """텍스트 임베딩"""
        return self._embedder.embed_texts(texts)
