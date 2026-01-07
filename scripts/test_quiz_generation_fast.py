import pytest
import sys
from pathlib import Path

# ============================================================
# Path 설정
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.repositories.milvus_quiz_repository import MilvusQuizRepository
from app.services.domain_quiz_batch_service import DomainQuizBatchService
from app.clients.milvus_client import get_milvus_client


# ============================================================
# Milvus 연결 (🔥 function scope 필수)
# ============================================================
@pytest.fixture(autouse=True)
async def milvus_connection():
    """
    pytest-asyncio 제약:
    - async fixture는 function scope만 허용
    - session scope ❌
    """
    client = get_milvus_client()
    await client._ensure_connection()
    await client._get_collection_async()
    yield


# ============================================================
# Dummy Quiz Service (🔥 LLM 호출 없음)
# ============================================================
class DummyQuizService:
    async def generate_quiz(self, *args, **kwargs):
        return None

    def get_last_qc_result(self):
        return None


# ============================================================
# FAST Smoke Test
# ============================================================
@pytest.mark.asyncio
async def test_domain_pipeline_fast():
    """
    [FAST TEST]
    - Milvus 연결
    - dataset_id 필터링
    - 파이프라인 구조 검증
    - ❌ LLM 호출 없음
    """

    repo = MilvusQuizRepository(
        collection_name="ragflow_chunks_openai"
    )

    batch_service = DomainQuizBatchService(
        milvus_repo=repo,
        quiz_service=DummyQuizService(),
    )

    blocks = repo.fetch_blocks_by_domain(
        dataset_id="직장내괴롭힘교육",
        limit=5,
    )

    assert isinstance(blocks, list)
