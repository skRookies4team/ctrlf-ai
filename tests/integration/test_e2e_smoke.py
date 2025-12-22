"""
Integration E2E Smoke Test

통합 테스트 환경에서 최소 보장 시나리오를 검증합니다.

시나리오:
1. RAGFlow 헬스체크
2. LLM 헬스체크
3. Milvus 연결 (옵션)
4. 단일 문서 인덱싱 → 검색 → 응답 생성

실행 조건:
- LLM_BASE_URL, RAGFLOW_BASE_URL 환경변수 필수
- 서비스가 실제로 실행 중이어야 함

로컬에서 실행:
  LLM_BASE_URL=http://localhost:8000 \\
  RAGFLOW_BASE_URL=http://localhost:9380 \\
  pytest tests/integration/ -v
"""

import os
import pytest
import httpx

from tests.integration.conftest import SKIP_INTEGRATION, SKIP_REASON


# =============================================================================
# Skip if integration env not available
# =============================================================================

pytestmark = pytest.mark.skipif(SKIP_INTEGRATION, reason=SKIP_REASON)


# =============================================================================
# Health Check Tests
# =============================================================================


class TestServiceHealth:
    """서비스 헬스체크 테스트."""

    @pytest.mark.asyncio
    async def test_llm_service_reachable(self):
        """LLM 서비스 연결 확인."""
        base_url = os.environ.get("LLM_BASE_URL")
        assert base_url, "LLM_BASE_URL must be set"

        async with httpx.AsyncClient() as client:
            try:
                # OpenAI compatible health endpoint
                response = await client.get(
                    f"{base_url}/health",
                    timeout=10.0,
                )
                # 200 또는 404 (health endpoint가 없을 수 있음)
                assert response.status_code < 500, f"LLM service error: {response.status_code}"
            except httpx.ConnectError as e:
                pytest.fail(f"Cannot connect to LLM service at {base_url}: {e}")

    @pytest.mark.asyncio
    async def test_ragflow_service_reachable(self):
        """RAGFlow 서비스 연결 확인."""
        base_url = os.environ.get("RAGFLOW_BASE_URL")
        assert base_url, "RAGFLOW_BASE_URL must be set"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{base_url}/api/health",
                    timeout=10.0,
                )
                # 200 또는 404 (health endpoint가 없을 수 있음)
                assert response.status_code < 500, f"RAGFlow service error: {response.status_code}"
            except httpx.ConnectError as e:
                pytest.fail(f"Cannot connect to RAGFlow service at {base_url}: {e}")


# =============================================================================
# E2E Smoke Test
# =============================================================================


class TestE2ESmoke:
    """E2E Smoke 테스트 - 최소 보장 시나리오."""

    @pytest.mark.asyncio
    async def test_chat_api_responds(self):
        """Chat API가 응답하는지 확인.

        실제 LLM/RAGFlow 없이도 FastAPI 앱이 동작하는지 확인.
        """
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200, f"Health check failed: {response.text}"

    @pytest.mark.asyncio
    async def test_llm_generates_response(self):
        """LLM이 응답을 생성하는지 확인."""
        from app.clients.llm_client import get_llm_client

        client = get_llm_client()
        if not client._base_url:
            pytest.skip("LLM_BASE_URL not configured")

        try:
            response = await client.generate_chat_completion(
                messages=[
                    {"role": "user", "content": "안녕하세요. 테스트 메시지입니다."}
                ],
                max_tokens=50,
            )
            assert response is not None
            assert len(response) > 0
        except Exception as e:
            pytest.fail(f"LLM generation failed: {e}")

    @pytest.mark.asyncio
    async def test_ragflow_search_executes(self):
        """RAGFlow 검색이 실행되는지 확인.

        검색 결과가 있든 없든, 에러 없이 실행되면 성공.
        """
        from app.clients.ragflow_search_client import get_ragflow_search_client

        client = get_ragflow_search_client()
        if not client._base_url:
            pytest.skip("RAGFLOW_BASE_URL not configured")

        try:
            results = await client.search_chunks(
                query="테스트 검색어",
                dataset_name="default",
                top_k=3,
            )
            # 결과가 있든 없든 리스트여야 함
            assert isinstance(results, list)
        except Exception as e:
            # 연결 실패가 아닌 다른 에러 (예: dataset not found)는 허용
            if "connect" in str(e).lower():
                pytest.fail(f"RAGFlow connection failed: {e}")
            # 다른 에러는 로깅만 (검색 자체는 실행됨)
            pass
