"""
RAG Documents Ingest API 테스트

사내규정(POLICY) 문서 Ingest 기능 테스트.

테스트 케이스:
1. POST /internal/ai/rag-documents/ingest
   - 정상 요청 (202 + PROCESSING)
   - 중복 요청 - 처리 중 (202 + PROCESSING)
   - 중복 요청 - 완료됨 (200 + COMPLETED)
   - 잘못된 도메인 (400 INVALID_DOMAIN)
   - 토큰 누락 (401 UNAUTHORIZED)
   - 토큰 불일치 (401 UNAUTHORIZED)

2. POST /internal/ai/callbacks/ragflow/ingest
   - 정상 콜백 (200)
   - 캐시 상태 업데이트 확인
   - Backend 클라이언트 호출 확인

3. RAGFlowIngestClient
   - 정상 호출
   - 타임아웃
   - 네트워크 에러
   - 401/400/500 에러

4. BackendClient.update_rag_document_status
   - 정상 호출
   - 에러 처리
"""

import time

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.api.v1.rag_documents import (
    _ingest_cache,
    _mark_request_processing,
    _mark_request_completed,
    _get_cached_status,
    _clear_request_cache,
    _get_ttl_for_status,
    _enforce_cache_size_limit,
    _get_cache_stats,
    _CACHE_TTL_PROCESSING_SECONDS,
    _CACHE_TTL_COMPLETED_SECONDS,
    _CACHE_MAX_SIZE,
    ALLOWED_DOMAINS,
    DOMAIN_DATASET_MAPPING,
)
from app.clients.ragflow_ingest_client import (
    RAGFlowIngestClient,
    RAGFlowIngestError,
    RAGFlowUnavailableError,
    get_ragflow_ingest_client,
    clear_ragflow_ingest_client,
)
from app.clients.backend_client import (
    RAGDocumentStatusUpdateError,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clear_cache():
    """테스트 전후 캐시 초기화."""
    _ingest_cache.clear()
    clear_ragflow_ingest_client()
    yield
    _ingest_cache.clear()
    clear_ragflow_ingest_client()


@pytest.fixture
def client():
    """FastAPI TestClient."""
    return TestClient(app)


@pytest.fixture
def mock_settings_no_token():
    """토큰 미설정 mock settings."""
    mock = MagicMock()
    mock.BACKEND_INTERNAL_TOKEN = None
    mock.RAGFLOW_CALLBACK_TOKEN = None
    mock.ragflow_base_url = "http://ragflow:8080"
    mock.backend_base_url = "http://backend:8080"
    return mock


@pytest.fixture
def mock_settings_with_token():
    """토큰 설정된 mock settings."""
    mock = MagicMock()
    mock.BACKEND_INTERNAL_TOKEN = "valid-backend-token"
    mock.RAGFLOW_CALLBACK_TOKEN = "valid-ragflow-token"
    mock.ragflow_base_url = "http://ragflow:8080"
    mock.backend_base_url = "http://backend:8080"
    return mock


@pytest.fixture
def sample_ingest_request():
    """샘플 ingest 요청."""
    return {
        "ragDocumentPk": "pk-001",
        "documentId": "POL-TEST-001",
        "version": 1,
        "sourceUrl": "https://s3.example.com/doc.pdf",
        "domain": "POLICY",
        "requestId": "req-001",
        "traceId": "trace-001",
    }


@pytest.fixture
def sample_callback_request():
    """샘플 콜백 요청."""
    return {
        "ingestId": "ingest-001",
        "docId": "POL-TEST-001",
        "version": 1,
        "status": "COMPLETED",
        "processedAt": "2025-12-29T12:00:00Z",
        "failReason": None,
        "meta": {
            "ragDocumentPk": "pk-001",
            "traceId": "trace-001",
            "requestId": "req-001",
        },
        "stats": {"chunks": 42},
    }


# =============================================================================
# Cache Helper Tests
# =============================================================================


class TestCacheHelpers:
    """캐시 헬퍼 함수 테스트."""

    def test_mark_request_processing(self):
        """처리 중 상태 표시 테스트."""
        _mark_request_processing("DOC-001", 1)
        cached = _get_cached_status("DOC-001", 1)
        assert cached is not None
        assert cached["status"] == "PROCESSING"

    def test_mark_request_completed(self):
        """완료 상태 표시 테스트."""
        _mark_request_completed("DOC-001", 1, "COMPLETED")
        cached = _get_cached_status("DOC-001", 1)
        assert cached is not None
        assert cached["status"] == "COMPLETED"

    def test_mark_request_failed(self):
        """실패 상태 표시 테스트."""
        _mark_request_completed("DOC-001", 1, "FAILED")
        cached = _get_cached_status("DOC-001", 1)
        assert cached is not None
        assert cached["status"] == "FAILED"

    def test_clear_request_cache(self):
        """캐시 삭제 테스트."""
        _mark_request_processing("DOC-001", 1)
        _clear_request_cache("DOC-001", 1)
        cached = _get_cached_status("DOC-001", 1)
        assert cached is None

    def test_different_versions_separate_cache(self):
        """다른 버전은 별도 캐시 테스트."""
        _mark_request_processing("DOC-001", 1)
        _mark_request_completed("DOC-001", 2, "COMPLETED")

        cached_v1 = _get_cached_status("DOC-001", 1)
        cached_v2 = _get_cached_status("DOC-001", 2)

        assert cached_v1["status"] == "PROCESSING"
        assert cached_v2["status"] == "COMPLETED"


# =============================================================================
# 2-Tier TTL Cache Tests
# =============================================================================


class TestTwoTierTTLCache:
    """2단계 TTL 캐시 테스트."""

    def test_ttl_for_processing_is_5_minutes(self):
        """PROCESSING 상태의 TTL은 5분(300초)."""
        ttl = _get_ttl_for_status("PROCESSING")
        assert ttl == 300
        assert ttl == _CACHE_TTL_PROCESSING_SECONDS

    def test_ttl_for_completed_is_24_hours(self):
        """COMPLETED 상태의 TTL은 24시간(86400초)."""
        ttl = _get_ttl_for_status("COMPLETED")
        assert ttl == 86400
        assert ttl == _CACHE_TTL_COMPLETED_SECONDS

    def test_ttl_for_failed_is_24_hours(self):
        """FAILED 상태의 TTL은 24시간(86400초)."""
        ttl = _get_ttl_for_status("FAILED")
        assert ttl == 86400
        assert ttl == _CACHE_TTL_COMPLETED_SECONDS

    def test_processing_expires_after_5_minutes(self):
        """PROCESSING 상태는 5분 후 만료."""
        _mark_request_processing("DOC-TTL-001", 1)

        # 5분 전: 캐시 유효
        with patch("app.api.v1.rag_documents.time.time", return_value=time.time()):
            cached = _get_cached_status("DOC-TTL-001", 1)
            assert cached is not None

        # 5분 후: 캐시 만료
        with patch("app.api.v1.rag_documents.time.time", return_value=time.time() + 301):
            cached = _get_cached_status("DOC-TTL-001", 1)
            assert cached is None

    def test_completed_persists_for_24_hours(self):
        """COMPLETED 상태는 24시간 동안 유지."""
        _mark_request_completed("DOC-TTL-002", 1, "COMPLETED")

        # 23시간 후: 캐시 유효
        with patch("app.api.v1.rag_documents.time.time", return_value=time.time() + 82800):
            cached = _get_cached_status("DOC-TTL-002", 1)
            assert cached is not None
            assert cached["status"] == "COMPLETED"

        # 24시간 후: 캐시 만료
        with patch("app.api.v1.rag_documents.time.time", return_value=time.time() + 86401):
            cached = _get_cached_status("DOC-TTL-002", 1)
            assert cached is None

    def test_cache_stats(self):
        """캐시 통계 테스트."""
        _mark_request_processing("DOC-STATS-001", 1)
        _mark_request_processing("DOC-STATS-002", 1)
        _mark_request_completed("DOC-STATS-003", 1, "COMPLETED")
        _mark_request_completed("DOC-STATS-004", 1, "FAILED")

        stats = _get_cache_stats()

        assert stats["total"] == 4
        assert stats["processing"] == 2
        assert stats["completed"] == 1
        assert stats["failed"] == 1


# =============================================================================
# LRU Cache Size Limit Tests
# =============================================================================


class TestLRUCacheSizeLimit:
    """LRU 캐시 크기 제한 테스트."""

    def test_cache_max_size_constant(self):
        """최대 캐시 크기 상수 확인."""
        assert _CACHE_MAX_SIZE == 10000

    def test_enforce_cache_size_limit_removes_oldest(self):
        """캐시 크기 제한 시 가장 오래된 항목 삭제."""
        # 캐시 초기화
        _ingest_cache.clear()

        # 시간 순서대로 캐시 추가
        base_time = time.time()
        for i in range(5):
            _ingest_cache[(f"DOC-LRU-{i}", 1)] = {
                "timestamp": base_time + i,
                "status": "PROCESSING",
            }

        assert len(_ingest_cache) == 5

        # 최대 크기를 3으로 임시 설정하여 테스트
        with patch("app.api.v1.rag_documents._CACHE_MAX_SIZE", 3):
            _enforce_cache_size_limit()

        # 가장 오래된 2개 삭제됨
        assert len(_ingest_cache) == 3
        assert ("DOC-LRU-0", 1) not in _ingest_cache
        assert ("DOC-LRU-1", 1) not in _ingest_cache
        assert ("DOC-LRU-2", 1) in _ingest_cache
        assert ("DOC-LRU-3", 1) in _ingest_cache
        assert ("DOC-LRU-4", 1) in _ingest_cache

    def test_mark_processing_enforces_size_limit(self):
        """_mark_request_processing이 크기 제한을 적용."""
        _ingest_cache.clear()

        # 최대 크기를 2로 임시 설정
        with patch("app.api.v1.rag_documents._CACHE_MAX_SIZE", 2):
            base_time = time.time()

            # 시간차를 두고 캐시 추가
            with patch("app.api.v1.rag_documents.time.time", return_value=base_time):
                _mark_request_processing("DOC-SIZE-1", 1)

            with patch("app.api.v1.rag_documents.time.time", return_value=base_time + 1):
                _mark_request_processing("DOC-SIZE-2", 1)

            with patch("app.api.v1.rag_documents.time.time", return_value=base_time + 2):
                _mark_request_processing("DOC-SIZE-3", 1)

            # 최대 2개만 유지 (가장 오래된 1개 삭제)
            assert len(_ingest_cache) == 2
            assert ("DOC-SIZE-1", 1) not in _ingest_cache


# =============================================================================
# Constants Tests
# =============================================================================


class TestConstants:
    """상수 테스트."""

    def test_allowed_domains(self):
        """허용 도메인 테스트."""
        assert "POLICY" in ALLOWED_DOMAINS
        assert "EDUCATION" not in ALLOWED_DOMAINS

    def test_domain_dataset_mapping(self):
        """도메인 → dataset 매핑 테스트."""
        assert DOMAIN_DATASET_MAPPING["POLICY"] == "사내규정"


# =============================================================================
# Ingest Endpoint Tests (No Token Required)
# =============================================================================


class TestIngestEndpointNoToken:
    """토큰 미설정 환경에서의 ingest 엔드포인트 테스트."""

    def test_ingest_success_202(self, client, sample_ingest_request):
        """정상 ingest 요청 - 202 반환."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings, \
             patch("app.api.v1.rag_documents.get_ragflow_ingest_client") as mock_client:
            mock_get_settings.return_value.BACKEND_INTERNAL_TOKEN = None
            mock_client.return_value.ingest = AsyncMock()

            response = client.post(
                "/internal/ai/rag-documents/ingest",
                json=sample_ingest_request,
            )

            assert response.status_code == 202
            data = response.json()
            assert data["received"] is True
            assert data["status"] == "PROCESSING"
            assert data["documentId"] == "POL-TEST-001"
            assert data["version"] == 1

    def test_ingest_duplicate_processing_202(self, client, sample_ingest_request):
        """중복 요청 (처리 중) - 202 반환."""
        # 캐시에 처리 중으로 표시
        _mark_request_processing("POL-TEST-001", 1)

        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings:
            mock_get_settings.return_value.BACKEND_INTERNAL_TOKEN = None

            response = client.post(
                "/internal/ai/rag-documents/ingest",
                json=sample_ingest_request,
            )

            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "PROCESSING"

    def test_ingest_duplicate_completed_200(self, client, sample_ingest_request):
        """중복 요청 (이미 완료) - 200 반환."""
        # 캐시에 완료로 표시
        _mark_request_completed("POL-TEST-001", 1, "COMPLETED")

        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings:
            mock_get_settings.return_value.BACKEND_INTERNAL_TOKEN = None

            response = client.post(
                "/internal/ai/rag-documents/ingest",
                json=sample_ingest_request,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "COMPLETED"

    def test_ingest_invalid_domain_400(self, client, sample_ingest_request):
        """잘못된 도메인 - 400 INVALID_DOMAIN."""
        sample_ingest_request["domain"] = "EDUCATION"

        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings:
            mock_get_settings.return_value.BACKEND_INTERNAL_TOKEN = None

            response = client.post(
                "/internal/ai/rag-documents/ingest",
                json=sample_ingest_request,
            )

            assert response.status_code == 400
            data = response.json()
            assert data["error"] == "INVALID_DOMAIN"
            assert "EDUCATION" in data["message"]
            assert data["traceId"] == "trace-001"

    def test_ingest_new_version_separate(self, client, sample_ingest_request):
        """새 버전은 별도 처리."""
        # 버전 1 완료
        _mark_request_completed("POL-TEST-001", 1, "COMPLETED")

        # 버전 2 요청
        sample_ingest_request["version"] = 2

        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings, \
             patch("app.api.v1.rag_documents.get_ragflow_ingest_client") as mock_client:
            mock_get_settings.return_value.BACKEND_INTERNAL_TOKEN = None
            mock_client.return_value.ingest = AsyncMock()

            response = client.post(
                "/internal/ai/rag-documents/ingest",
                json=sample_ingest_request,
            )

            assert response.status_code == 202
            data = response.json()
            assert data["version"] == 2
            assert data["status"] == "PROCESSING"


# =============================================================================
# Ingest Endpoint Tests (Token Required)
# =============================================================================


class TestIngestEndpointWithToken:
    """토큰 설정된 환경에서의 ingest 엔드포인트 테스트."""

    def test_ingest_missing_token_401(self, client, sample_ingest_request):
        """토큰 누락 - 401 UNAUTHORIZED."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings:
            mock_get_settings.return_value.BACKEND_INTERNAL_TOKEN = "valid-token"

            response = client.post(
                "/internal/ai/rag-documents/ingest",
                json=sample_ingest_request,
            )

            assert response.status_code == 401
            data = response.json()
            assert data["error"] == "UNAUTHORIZED"

    def test_ingest_invalid_token_401(self, client, sample_ingest_request):
        """잘못된 토큰 - 401 UNAUTHORIZED."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings:
            mock_get_settings.return_value.BACKEND_INTERNAL_TOKEN = "valid-token"

            response = client.post(
                "/internal/ai/rag-documents/ingest",
                json=sample_ingest_request,
                headers={"X-Internal-Token": "wrong-token"},
            )

            assert response.status_code == 401
            data = response.json()
            assert data["error"] == "UNAUTHORIZED"

    def test_ingest_valid_token_202(self, client, sample_ingest_request):
        """유효한 토큰 - 202 반환."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings, \
             patch("app.api.v1.rag_documents.get_ragflow_ingest_client") as mock_client:
            mock_get_settings.return_value.BACKEND_INTERNAL_TOKEN = "valid-token"
            mock_client.return_value.ingest = AsyncMock()

            response = client.post(
                "/internal/ai/rag-documents/ingest",
                json=sample_ingest_request,
                headers={"X-Internal-Token": "valid-token"},
            )

            assert response.status_code == 202


# =============================================================================
# Callback Endpoint Tests
# =============================================================================


class TestCallbackEndpoint:
    """콜백 엔드포인트 테스트 (토큰 미설정 환경)."""

    def test_callback_success_200(self, client, sample_callback_request):
        """정상 콜백 - 200 반환."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings, \
             patch("app.api.v1.rag_documents.get_backend_client") as mock_client:
            mock_get_settings.return_value.RAGFLOW_CALLBACK_TOKEN = None
            mock_client.return_value.update_rag_document_status = AsyncMock(return_value=True)

            response = client.post(
                "/internal/ai/callbacks/ragflow/ingest",
                json=sample_callback_request,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["received"] is True

    def test_callback_updates_cache_to_completed(self, client, sample_callback_request):
        """콜백 수신 시 캐시 상태 COMPLETED로 업데이트."""
        # 초기 상태: 처리 중
        _mark_request_processing("POL-TEST-001", 1)

        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings, \
             patch("app.api.v1.rag_documents.get_backend_client") as mock_client:
            mock_get_settings.return_value.RAGFLOW_CALLBACK_TOKEN = None
            mock_client.return_value.update_rag_document_status = AsyncMock(return_value=True)

            response = client.post(
                "/internal/ai/callbacks/ragflow/ingest",
                json=sample_callback_request,
            )

            assert response.status_code == 200

            # 캐시 상태 확인
            cached = _get_cached_status("POL-TEST-001", 1)
            assert cached is not None
            assert cached["status"] == "COMPLETED"

    def test_callback_updates_cache_to_failed(self, client, sample_callback_request):
        """콜백 수신 시 캐시 상태 FAILED로 업데이트."""
        sample_callback_request["status"] = "FAILED"
        sample_callback_request["failReason"] = "Parsing error"

        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings, \
             patch("app.api.v1.rag_documents.get_backend_client") as mock_client:
            mock_get_settings.return_value.RAGFLOW_CALLBACK_TOKEN = None
            mock_client.return_value.update_rag_document_status = AsyncMock(return_value=True)

            response = client.post(
                "/internal/ai/callbacks/ragflow/ingest",
                json=sample_callback_request,
            )

            assert response.status_code == 200

            # 캐시 상태 확인
            cached = _get_cached_status("POL-TEST-001", 1)
            assert cached["status"] == "FAILED"

    def test_callback_calls_backend_client(self, client, sample_callback_request):
        """콜백 수신 시 Backend 클라이언트 호출 확인."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings, \
             patch("app.api.v1.rag_documents.get_backend_client") as mock_get_client:
            mock_get_settings.return_value.RAGFLOW_CALLBACK_TOKEN = None
            mock_client = MagicMock()
            mock_client.update_rag_document_status = AsyncMock(return_value=True)
            mock_get_client.return_value = mock_client

            response = client.post(
                "/internal/ai/callbacks/ragflow/ingest",
                json=sample_callback_request,
            )

            assert response.status_code == 200
            mock_client.update_rag_document_status.assert_called_once_with(
                rag_document_pk="pk-001",
                status="COMPLETED",
                document_id="POL-TEST-001",
                version=1,
                processed_at="2025-12-29T12:00:00Z",
                fail_reason=None,
            )

    def test_callback_backend_error_still_200(self, client, sample_callback_request):
        """Backend 호출 실패해도 200 반환."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings, \
             patch("app.api.v1.rag_documents.get_backend_client") as mock_get_client:
            mock_get_settings.return_value.RAGFLOW_CALLBACK_TOKEN = None
            mock_client = MagicMock()
            mock_client.update_rag_document_status = AsyncMock(
                side_effect=RAGDocumentStatusUpdateError(
                    rag_document_pk="pk-001",
                    status_code=500,
                    message="Backend error",
                )
            )
            mock_get_client.return_value = mock_client

            response = client.post(
                "/internal/ai/callbacks/ragflow/ingest",
                json=sample_callback_request,
            )

            # Backend 실패해도 200 반환
            assert response.status_code == 200
            data = response.json()
            assert data["received"] is True


# =============================================================================
# Callback Endpoint Tests (Token Required)
# =============================================================================


class TestCallbackEndpointWithToken:
    """토큰 설정된 환경에서의 콜백 엔드포인트 테스트."""

    def test_callback_missing_token_401(self, client, sample_callback_request):
        """토큰 누락 - 401 UNAUTHORIZED."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings:
            mock_get_settings.return_value.RAGFLOW_CALLBACK_TOKEN = "valid-ragflow-token"

            response = client.post(
                "/internal/ai/callbacks/ragflow/ingest",
                json=sample_callback_request,
            )

            assert response.status_code == 401
            data = response.json()
            assert data["error"] == "UNAUTHORIZED"

    def test_callback_invalid_token_401(self, client, sample_callback_request):
        """잘못된 토큰 - 401 UNAUTHORIZED."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings:
            mock_get_settings.return_value.RAGFLOW_CALLBACK_TOKEN = "valid-ragflow-token"

            response = client.post(
                "/internal/ai/callbacks/ragflow/ingest",
                json=sample_callback_request,
                headers={"X-Internal-Token": "wrong-token"},
            )

            assert response.status_code == 401
            data = response.json()
            assert data["error"] == "UNAUTHORIZED"

    def test_callback_backend_token_rejected_401(self, client, sample_callback_request):
        """Backend 토큰으로 콜백 시도 - 401 (토큰 분리 확인)."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings:
            mock_get_settings.return_value.BACKEND_INTERNAL_TOKEN = "valid-backend-token"
            mock_get_settings.return_value.RAGFLOW_CALLBACK_TOKEN = "valid-ragflow-token"

            # Backend 토큰으로 콜백 시도 → 실패해야 함
            response = client.post(
                "/internal/ai/callbacks/ragflow/ingest",
                json=sample_callback_request,
                headers={"X-Internal-Token": "valid-backend-token"},
            )

            assert response.status_code == 401
            data = response.json()
            assert data["error"] == "UNAUTHORIZED"

    def test_callback_valid_token_200(self, client, sample_callback_request):
        """유효한 RAGFlow 토큰 - 200 반환."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings, \
             patch("app.api.v1.rag_documents.get_backend_client") as mock_client:
            mock_get_settings.return_value.RAGFLOW_CALLBACK_TOKEN = "valid-ragflow-token"
            mock_client.return_value.update_rag_document_status = AsyncMock(return_value=True)

            response = client.post(
                "/internal/ai/callbacks/ragflow/ingest",
                json=sample_callback_request,
                headers={"X-Internal-Token": "valid-ragflow-token"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["received"] is True


# =============================================================================
# RAGFlowIngestClient Tests
# =============================================================================


class TestRAGFlowIngestClient:
    """RAGFlowIngestClient 테스트."""

    @pytest.fixture
    def mock_http_client(self):
        """Mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    def test_is_configured_true(self):
        """URL 설정 시 is_configured=True."""
        client = RAGFlowIngestClient(base_url="http://ragflow:8080")
        assert client.is_configured is True

    def test_is_configured_false(self):
        """URL 미설정 시 is_configured=False."""
        with patch("app.clients.ragflow_ingest_client.get_settings") as mock:
            mock.return_value.ragflow_base_url = None
            mock.return_value.BACKEND_INTERNAL_TOKEN = None
            client = RAGFlowIngestClient(base_url=None)
            assert client.is_configured is False

    @pytest.mark.asyncio
    async def test_ingest_success_202(self, mock_http_client):
        """정상 ingest - 202 반환."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {"received": True, "ingestId": "ingest-001"}
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = RAGFlowIngestClient(
            base_url="http://ragflow:8080",
            client=mock_http_client,
        )

        result = await client.ingest(
            dataset_id="사내규정",
            doc_id="POL-001",
            version=1,
            file_url="https://s3.example.com/doc.pdf",
            rag_document_pk="pk-001",
            domain="POLICY",
            trace_id="trace-001",
            request_id="req-001",
        )

        assert result["received"] is True
        mock_http_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ingest_no_url_raises_error(self):
        """URL 미설정 시 RAGFlowUnavailableError."""
        with patch("app.clients.ragflow_ingest_client.get_settings") as mock:
            mock.return_value.ragflow_base_url = None
            mock.return_value.BACKEND_INTERNAL_TOKEN = None
            client = RAGFlowIngestClient(base_url=None)

            with pytest.raises(RAGFlowUnavailableError) as exc_info:
                await client.ingest(
                    dataset_id="사내규정",
                    doc_id="POL-001",
                    version=1,
                    file_url="https://s3.example.com/doc.pdf",
                    rag_document_pk="pk-001",
                    domain="POLICY",
                    trace_id="trace-001",
                    request_id="req-001",
                )

            assert "not configured" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_ingest_401_raises_error(self, mock_http_client):
        """401 응답 시 RAGFlowIngestError."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = RAGFlowIngestClient(
            base_url="http://ragflow:8080",
            client=mock_http_client,
        )

        with pytest.raises(RAGFlowIngestError) as exc_info:
            await client.ingest(
                dataset_id="사내규정",
                doc_id="POL-001",
                version=1,
                file_url="https://s3.example.com/doc.pdf",
                rag_document_pk="pk-001",
                domain="POLICY",
                trace_id="trace-001",
                request_id="req-001",
            )

        assert exc_info.value.status_code == 401
        assert "RAGFLOW_UNAUTHORIZED" in exc_info.value.error_code

    @pytest.mark.asyncio
    async def test_ingest_400_raises_error(self, mock_http_client):
        """400 응답 시 RAGFlowIngestError."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"message": "Invalid request"}
        mock_response.text = "Invalid request"
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = RAGFlowIngestClient(
            base_url="http://ragflow:8080",
            client=mock_http_client,
        )

        with pytest.raises(RAGFlowIngestError) as exc_info:
            await client.ingest(
                dataset_id="사내규정",
                doc_id="POL-001",
                version=1,
                file_url="https://s3.example.com/doc.pdf",
                rag_document_pk="pk-001",
                domain="POLICY",
                trace_id="trace-001",
                request_id="req-001",
            )

        assert exc_info.value.status_code == 400
        assert "RAGFLOW_BAD_REQUEST" in exc_info.value.error_code

    @pytest.mark.asyncio
    async def test_ingest_500_raises_unavailable(self, mock_http_client):
        """500 응답 시 RAGFlowUnavailableError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = RAGFlowIngestClient(
            base_url="http://ragflow:8080",
            client=mock_http_client,
        )

        with pytest.raises(RAGFlowUnavailableError):
            await client.ingest(
                dataset_id="사내규정",
                doc_id="POL-001",
                version=1,
                file_url="https://s3.example.com/doc.pdf",
                rag_document_pk="pk-001",
                domain="POLICY",
                trace_id="trace-001",
                request_id="req-001",
            )

    @pytest.mark.asyncio
    async def test_ingest_timeout_raises_unavailable(self, mock_http_client):
        """타임아웃 시 RAGFlowUnavailableError."""
        mock_http_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

        client = RAGFlowIngestClient(
            base_url="http://ragflow:8080",
            client=mock_http_client,
        )

        with pytest.raises(RAGFlowUnavailableError) as exc_info:
            await client.ingest(
                dataset_id="사내규정",
                doc_id="POL-001",
                version=1,
                file_url="https://s3.example.com/doc.pdf",
                rag_document_pk="pk-001",
                domain="POLICY",
                trace_id="trace-001",
                request_id="req-001",
            )

        assert "Timeout" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_ingest_network_error_raises_unavailable(self, mock_http_client):
        """네트워크 에러 시 RAGFlowUnavailableError."""
        mock_http_client.post = AsyncMock(
            side_effect=httpx.RequestError("Connection refused")
        )

        client = RAGFlowIngestClient(
            base_url="http://ragflow:8080",
            client=mock_http_client,
        )

        with pytest.raises(RAGFlowUnavailableError) as exc_info:
            await client.ingest(
                dataset_id="사내규정",
                doc_id="POL-001",
                version=1,
                file_url="https://s3.example.com/doc.pdf",
                rag_document_pk="pk-001",
                domain="POLICY",
                trace_id="trace-001",
                request_id="req-001",
            )

        assert "Network error" in str(exc_info.value)


# =============================================================================
# BackendClient.update_rag_document_status Tests
# =============================================================================


class TestBackendClientUpdateStatus:
    """BackendClient.update_rag_document_status 테스트."""

    @pytest.mark.asyncio
    async def test_update_status_success(self):
        """정상 상태 업데이트."""
        from app.clients.backend_client import BackendClient

        mock_http_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http_client.patch = AsyncMock(return_value=mock_response)

        client = BackendClient(
            base_url="http://backend:8080",
            client=mock_http_client,
        )

        result = await client.update_rag_document_status(
            rag_document_pk="pk-001",
            status="COMPLETED",
            document_id="POL-001",
            version=1,
            processed_at="2025-12-29T12:00:00Z",
        )

        assert result is True
        mock_http_client.patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_status_204_success(self):
        """204 응답도 성공."""
        from app.clients.backend_client import BackendClient

        mock_http_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_http_client.patch = AsyncMock(return_value=mock_response)

        client = BackendClient(
            base_url="http://backend:8080",
            client=mock_http_client,
        )

        result = await client.update_rag_document_status(
            rag_document_pk="pk-001",
            status="COMPLETED",
            document_id="POL-001",
            version=1,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_update_status_no_base_url(self):
        """URL 미설정 시 False 반환."""
        from app.clients.backend_client import BackendClient

        with patch("app.clients.backend_client.get_settings") as mock:
            mock.return_value.backend_base_url = None
            mock.return_value.BACKEND_INTERNAL_TOKEN = None
            mock.return_value.BACKEND_TIMEOUT_SEC = 30.0

            client = BackendClient(base_url=None)

            result = await client.update_rag_document_status(
                rag_document_pk="pk-001",
                status="COMPLETED",
                document_id="POL-001",
                version=1,
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_update_status_401_raises_error(self):
        """401 응답 시 RAGDocumentStatusUpdateError."""
        from app.clients.backend_client import BackendClient

        mock_http_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_http_client.patch = AsyncMock(return_value=mock_response)

        client = BackendClient(
            base_url="http://backend:8080",
            client=mock_http_client,
        )

        with pytest.raises(RAGDocumentStatusUpdateError) as exc_info:
            await client.update_rag_document_status(
                rag_document_pk="pk-001",
                status="COMPLETED",
                document_id="POL-001",
                version=1,
            )

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_update_status_404_raises_error(self):
        """404 응답 시 RAGDocumentStatusUpdateError."""
        from app.clients.backend_client import BackendClient

        mock_http_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_http_client.patch = AsyncMock(return_value=mock_response)

        client = BackendClient(
            base_url="http://backend:8080",
            client=mock_http_client,
        )

        with pytest.raises(RAGDocumentStatusUpdateError) as exc_info:
            await client.update_rag_document_status(
                rag_document_pk="pk-001",
                status="COMPLETED",
                document_id="POL-001",
                version=1,
            )

        assert exc_info.value.status_code == 404
        assert "RAG_DOCUMENT_NOT_FOUND" in exc_info.value.error_code

    @pytest.mark.asyncio
    async def test_update_status_500_raises_error(self):
        """500 응답 시 RAGDocumentStatusUpdateError."""
        from app.clients.backend_client import BackendClient

        mock_http_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_http_client.patch = AsyncMock(return_value=mock_response)

        client = BackendClient(
            base_url="http://backend:8080",
            client=mock_http_client,
        )

        with pytest.raises(RAGDocumentStatusUpdateError) as exc_info:
            await client.update_rag_document_status(
                rag_document_pk="pk-001",
                status="COMPLETED",
                document_id="POL-001",
                version=1,
            )

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_update_status_timeout_raises_error(self):
        """타임아웃 시 RAGDocumentStatusUpdateError."""
        from app.clients.backend_client import BackendClient

        mock_http_client = AsyncMock(spec=httpx.AsyncClient)
        mock_http_client.patch = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

        client = BackendClient(
            base_url="http://backend:8080",
            client=mock_http_client,
        )

        with pytest.raises(RAGDocumentStatusUpdateError) as exc_info:
            await client.update_rag_document_status(
                rag_document_pk="pk-001",
                status="COMPLETED",
                document_id="POL-001",
                version=1,
            )

        assert "Timeout" in exc_info.value.message


# =============================================================================
# Integration-like Tests (Full Flow)
# =============================================================================


class TestFullFlow:
    """전체 흐름 테스트."""

    def test_ingest_then_callback_then_reingest(self, client, sample_ingest_request, sample_callback_request):
        """ingest → callback → 재요청 전체 흐름."""
        with patch("app.api.v1.rag_documents.get_settings") as mock_get_settings, \
             patch("app.api.v1.rag_documents.get_ragflow_ingest_client") as mock_ragflow, \
             patch("app.api.v1.rag_documents.get_backend_client") as mock_backend:
            # ingest는 BACKEND_INTERNAL_TOKEN, callback은 RAGFLOW_CALLBACK_TOKEN 사용
            mock_get_settings.return_value.BACKEND_INTERNAL_TOKEN = None
            mock_get_settings.return_value.RAGFLOW_CALLBACK_TOKEN = None
            mock_ragflow.return_value.ingest = AsyncMock()
            mock_backend.return_value.update_rag_document_status = AsyncMock(return_value=True)

            # 1. 최초 ingest 요청 → 202
            response1 = client.post(
                "/internal/ai/rag-documents/ingest",
                json=sample_ingest_request,
            )
            assert response1.status_code == 202
            assert response1.json()["status"] == "PROCESSING"

            # 2. 중복 요청 → 202 (처리 중)
            response2 = client.post(
                "/internal/ai/rag-documents/ingest",
                json=sample_ingest_request,
            )
            assert response2.status_code == 202
            assert response2.json()["status"] == "PROCESSING"

            # 3. 콜백 수신 → 200
            response3 = client.post(
                "/internal/ai/callbacks/ragflow/ingest",
                json=sample_callback_request,
            )
            assert response3.status_code == 200

            # 4. 완료 후 재요청 → 200 (완료됨)
            response4 = client.post(
                "/internal/ai/rag-documents/ingest",
                json=sample_ingest_request,
            )
            assert response4.status_code == 200
            assert response4.json()["status"] == "COMPLETED"
