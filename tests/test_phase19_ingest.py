"""
문서 인덱싱 API 테스트 (Phase 19)

POST /ingest 엔드포인트 및 관련 서비스/클라이언트를 테스트합니다.

테스트 시나리오:
1. 모델 테스트 - IngestRequest/Response 검증
2. 서비스 테스트 - IngestService 로직 검증
3. 클라이언트 테스트 - RagflowClient.ingest_document 검증
4. API 테스트 - /ingest 엔드포인트 검증
"""

import json
from typing import Any, Dict

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.ingest import get_ingest_service
from app.clients.ragflow_client import RagflowClient
from app.main import app
from app.models.ingest import IngestRequest, IngestResponse, IngestStatusType
from app.services.ingest_service import IngestService, SourceTypeNotFoundError


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


# =============================================================================
# 1. 모델 테스트 (IngestRequest / IngestResponse)
# =============================================================================


class TestIngestModels:
    """IngestRequest/Response 모델 테스트"""

    def test_ingest_request_required_fields(self) -> None:
        """필수 필드만으로 IngestRequest 생성"""
        request = IngestRequest(
            doc_id="DOC-2025-00123",
            source_type="policy",
            storage_url="https://files.internal/documents/DOC-2025-00123.pdf",
            file_name="정보보안규정_v3.pdf",
            mime_type="application/pdf",
        )

        assert request.doc_id == "DOC-2025-00123"
        assert request.source_type == "policy"
        assert str(request.storage_url) == "https://files.internal/documents/DOC-2025-00123.pdf"
        assert request.file_name == "정보보안규정_v3.pdf"
        assert request.mime_type == "application/pdf"

        # 기본값 확인
        assert request.department is None
        assert request.acl == []
        assert request.tags == []
        assert request.version == 1

    def test_ingest_request_all_fields(self) -> None:
        """모든 필드로 IngestRequest 생성"""
        request = IngestRequest(
            doc_id="DOC-2025-00123",
            source_type="policy",
            storage_url="https://files.internal/documents/DOC-2025-00123.pdf",
            file_name="정보보안규정_v3.pdf",
            mime_type="application/pdf",
            department="DEV",
            acl=["ROLE_EMPLOYEE", "DEPT_DEV"],
            tags=["보안", "사규", "4대교육"],
            version=3,
        )

        assert request.department == "DEV"
        assert request.acl == ["ROLE_EMPLOYEE", "DEPT_DEV"]
        assert request.tags == ["보안", "사규", "4대교육"]
        assert request.version == 3

    def test_ingest_request_missing_required_field(self) -> None:
        """필수 필드 누락 시 ValidationError 발생"""
        with pytest.raises(ValidationError):
            IngestRequest(
                # doc_id 누락
                source_type="policy",
                storage_url="https://files.internal/doc.pdf",
                file_name="test.pdf",
                mime_type="application/pdf",
            )

    def test_ingest_request_empty_doc_id(self) -> None:
        """빈 doc_id 시 ValidationError 발생"""
        with pytest.raises(ValidationError):
            IngestRequest(
                doc_id="",  # 빈 문자열
                source_type="policy",
                storage_url="https://files.internal/doc.pdf",
                file_name="test.pdf",
                mime_type="application/pdf",
            )

    def test_ingest_request_invalid_url(self) -> None:
        """잘못된 URL 형식 시 ValidationError 발생"""
        with pytest.raises(ValidationError):
            IngestRequest(
                doc_id="DOC-001",
                source_type="policy",
                storage_url="not-a-valid-url",  # 잘못된 URL
                file_name="test.pdf",
                mime_type="application/pdf",
            )

    def test_ingest_request_version_min_value(self) -> None:
        """version은 1 이상이어야 함"""
        with pytest.raises(ValidationError):
            IngestRequest(
                doc_id="DOC-001",
                source_type="policy",
                storage_url="https://files.internal/doc.pdf",
                file_name="test.pdf",
                mime_type="application/pdf",
                version=0,  # 0은 허용 안됨
            )

    def test_ingest_response_done(self) -> None:
        """DONE 상태의 IngestResponse 생성"""
        response = IngestResponse(
            task_id="ingest-2025-000001",
            status="DONE",
        )

        assert response.task_id == "ingest-2025-000001"
        assert response.status == "DONE"

    def test_ingest_response_all_statuses(self) -> None:
        """모든 상태값 테스트"""
        statuses: list[IngestStatusType] = ["DONE", "QUEUED", "PROCESSING", "FAILED"]

        for status in statuses:
            response = IngestResponse(task_id=f"task-{status}", status=status)
            assert response.status == status

    def test_ingest_response_invalid_status(self) -> None:
        """잘못된 상태값 시 ValidationError 발생"""
        with pytest.raises(ValidationError):
            IngestResponse(
                task_id="task-001",
                status="INVALID_STATUS",  # type: ignore
            )


# =============================================================================
# 2. 서비스 테스트 (IngestService)
# =============================================================================


class TestIngestService:
    """IngestService 테스트"""

    def test_validate_source_type_valid(self) -> None:
        """유효한 source_type 검증 통과"""
        # Mock RagflowClient 사용
        mock_client = RagflowClient(base_url="http://test:8080")
        service = IngestService(ragflow_client=mock_client)

        # 예외가 발생하지 않아야 함
        service.validate_source_type("policy")
        service.validate_source_type("POLICY")
        service.validate_source_type("incident")
        service.validate_source_type("EDUCATION")

    def test_validate_source_type_invalid(self) -> None:
        """유효하지 않은 source_type 시 SourceTypeNotFoundError 발생"""
        mock_client = RagflowClient(base_url="http://test:8080")
        service = IngestService(ragflow_client=mock_client)

        with pytest.raises(SourceTypeNotFoundError) as exc_info:
            service.validate_source_type("unknown_type")

        assert exc_info.value.source_type == "unknown_type"
        assert "policy" in exc_info.value.available_types

    @pytest.mark.anyio
    async def test_ingest_success(self) -> None:
        """정상 인덱싱 요청 테스트"""
        captured_request: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_request["url"] = str(request.url)
            captured_request["payload"] = json.loads(request.content)
            return httpx.Response(
                status_code=200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "ingest-task-001",
                        "status": "DONE",
                    },
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            ragflow = RagflowClient(base_url="http://test-ragflow:8080", client=client)
            service = IngestService(ragflow_client=ragflow)

            ingest_request = IngestRequest(
                doc_id="DOC-001",
                source_type="policy",
                storage_url="https://files.internal/doc.pdf",
                file_name="test.pdf",
                mime_type="application/pdf",
                department="DEV",
                acl=["ROLE_EMPLOYEE"],
                tags=["보안"],
                version=2,
            )

            response = await service.ingest(ingest_request)

        # 응답 검증
        assert response.task_id == "ingest-task-001"
        assert response.status == "DONE"

        # 요청 payload 검증
        payload = captured_request["payload"]
        assert payload["kb_id"] == "kb_policy_001"
        assert payload["doc_id"] == "DOC-001"
        assert payload["file_name"] == "test.pdf"
        assert payload["mime_type"] == "application/pdf"
        assert payload["metadata"]["department"] == "DEV"
        assert payload["metadata"]["acl"] == ["ROLE_EMPLOYEE"]
        assert payload["metadata"]["tags"] == ["보안"]
        assert payload["metadata"]["version"] == 2

    @pytest.mark.anyio
    async def test_ingest_invalid_source_type(self) -> None:
        """잘못된 source_type으로 인덱싱 요청 시 예외 발생"""
        mock_client = RagflowClient(base_url="http://test:8080")
        service = IngestService(ragflow_client=mock_client)

        ingest_request = IngestRequest(
            doc_id="DOC-001",
            source_type="unknown_type",
            storage_url="https://files.internal/doc.pdf",
            file_name="test.pdf",
            mime_type="application/pdf",
        )

        with pytest.raises(SourceTypeNotFoundError):
            await service.ingest(ingest_request)


# =============================================================================
# 3. 클라이언트 테스트 (RagflowClient.ingest_document)
# =============================================================================


class TestRagflowClientIngest:
    """RagflowClient.ingest_document 테스트"""

    @pytest.mark.anyio
    async def test_ingest_document_success(self) -> None:
        """정상적인 인덱싱 응답 파싱"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "task-12345",
                        "status": "DONE",
                    },
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            ragflow = RagflowClient(base_url="http://test:8080", client=client)

            ingest_request = IngestRequest(
                doc_id="DOC-001",
                source_type="policy",
                storage_url="https://files.internal/doc.pdf",
                file_name="test.pdf",
                mime_type="application/pdf",
            )

            response = await ragflow.ingest_document(ingest_request)

        assert response.task_id == "task-12345"
        assert response.status == "DONE"

    @pytest.mark.anyio
    async def test_ingest_document_queued_status(self) -> None:
        """QUEUED 상태 응답 테스트"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "task-queued-001",
                        "status": "QUEUED",
                    },
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            ragflow = RagflowClient(base_url="http://test:8080", client=client)

            ingest_request = IngestRequest(
                doc_id="DOC-001",
                source_type="training",
                storage_url="https://files.internal/doc.pdf",
                file_name="training.pdf",
                mime_type="application/pdf",
            )

            response = await ragflow.ingest_document(ingest_request)

        assert response.status == "QUEUED"

    @pytest.mark.anyio
    async def test_ingest_document_error_code(self) -> None:
        """RAGFlow 에러 코드 응답 처리"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={
                    "code": 1,  # 에러 코드
                    "message": "Invalid document format",
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            ragflow = RagflowClient(base_url="http://test:8080", client=client)

            ingest_request = IngestRequest(
                doc_id="DOC-001",
                source_type="policy",
                storage_url="https://files.internal/doc.pdf",
                file_name="test.pdf",
                mime_type="application/pdf",
            )

            response = await ragflow.ingest_document(ingest_request)

        # code != 0이면 FAILED 상태
        assert response.status == "FAILED"
        assert "error-DOC-001" in response.task_id

    @pytest.mark.anyio
    async def test_ingest_document_http_error(self) -> None:
        """HTTP 5xx 에러 시 UpstreamServiceError 발생"""
        from app.core.exceptions import UpstreamServiceError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=500,
                json={"error": "Internal Server Error"},
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            ragflow = RagflowClient(base_url="http://test:8080", client=client)

            ingest_request = IngestRequest(
                doc_id="DOC-001",
                source_type="policy",
                storage_url="https://files.internal/doc.pdf",
                file_name="test.pdf",
                mime_type="application/pdf",
            )

            with pytest.raises(UpstreamServiceError) as exc_info:
                await ragflow.ingest_document(ingest_request)

            assert exc_info.value.status_code == 500

    @pytest.mark.anyio
    async def test_ingest_document_timeout(self) -> None:
        """타임아웃 시 UpstreamServiceError 발생"""
        from app.core.exceptions import UpstreamServiceError

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Connection timed out")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            ragflow = RagflowClient(
                base_url="http://test:8080",
                client=client,
                timeout=1.0,
            )

            ingest_request = IngestRequest(
                doc_id="DOC-001",
                source_type="policy",
                storage_url="https://files.internal/doc.pdf",
                file_name="test.pdf",
                mime_type="application/pdf",
            )

            with pytest.raises(UpstreamServiceError) as exc_info:
                await ragflow.ingest_document(ingest_request)

            assert exc_info.value.is_timeout is True

    @pytest.mark.anyio
    async def test_ingest_document_no_base_url(self) -> None:
        """base_url 미설정 시 FAILED 상태 반환"""
        ragflow = RagflowClient(base_url="")

        ingest_request = IngestRequest(
            doc_id="DOC-001",
            source_type="policy",
            storage_url="https://files.internal/doc.pdf",
            file_name="test.pdf",
            mime_type="application/pdf",
        )

        response = await ragflow.ingest_document(ingest_request)

        assert response.status == "FAILED"
        assert "skip-DOC-001" in response.task_id

    def test_source_type_to_kb_id_mapping(self) -> None:
        """source_type → kb_id 매핑 테스트"""
        ragflow = RagflowClient(base_url="http://test:8080")

        assert ragflow._source_type_to_kb_id("policy") == "kb_policy_001"
        assert ragflow._source_type_to_kb_id("POLICY") == "kb_policy_001"
        assert ragflow._source_type_to_kb_id("incident") == "kb_incident_001"
        assert ragflow._source_type_to_kb_id("education") == "kb_education_001"

        # 알 수 없는 값은 기본값(POLICY)으로 fallback
        assert ragflow._source_type_to_kb_id("unknown") == "kb_policy_001"

    def test_is_valid_source_type(self) -> None:
        """source_type 유효성 검사 테스트"""
        ragflow = RagflowClient(base_url="http://test:8080")

        assert ragflow.is_valid_source_type("policy") is True
        assert ragflow.is_valid_source_type("POLICY") is True
        assert ragflow.is_valid_source_type("incident") is True
        assert ragflow.is_valid_source_type("EDUCATION") is True
        assert ragflow.is_valid_source_type("unknown") is False
        assert ragflow.is_valid_source_type("") is False


# =============================================================================
# 4. API 테스트 (/ingest 엔드포인트)
# =============================================================================


class TestIngestApi:
    """POST /ingest API 테스트"""

    @pytest.fixture
    def client(self) -> TestClient:
        """FastAPI TestClient 생성"""
        return TestClient(app)

    @pytest.fixture(autouse=True)
    def reset_service(self) -> None:
        """테스트마다 서비스 인스턴스 초기화"""
        from app.api.v1 import ingest

        ingest._ingest_service = None

    def test_ingest_endpoint_exists(self, client: TestClient) -> None:
        """POST /ingest 엔드포인트 존재 확인 (422는 필드 누락)"""
        response = client.post("/ingest", json={})
        # 빈 요청은 필드 누락으로 422
        assert response.status_code == 422

    def test_ingest_validation_error_missing_fields(self, client: TestClient) -> None:
        """필수 필드 누락 시 422 에러"""
        response = client.post(
            "/ingest",
            json={
                "doc_id": "DOC-001",
                # source_type, storage_url, file_name, mime_type 누락
            },
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    def test_ingest_validation_error_invalid_url(self, client: TestClient) -> None:
        """잘못된 URL 형식 시 422 에러"""
        response = client.post(
            "/ingest",
            json={
                "doc_id": "DOC-001",
                "source_type": "policy",
                "storage_url": "not-a-url",
                "file_name": "test.pdf",
                "mime_type": "application/pdf",
            },
        )

        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_ingest_source_type_not_found(self) -> None:
        """알 수 없는 source_type 시 400 에러"""
        # Mock을 사용한 통합 테스트
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json={"code": 0, "data": {}})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            ragflow = RagflowClient(base_url="http://test:8080", client=http_client)
            service = IngestService(ragflow_client=ragflow)

            ingest_request = IngestRequest(
                doc_id="DOC-001",
                source_type="unknown_type",
                storage_url="https://files.internal/doc.pdf",
                file_name="test.pdf",
                mime_type="application/pdf",
            )

            with pytest.raises(SourceTypeNotFoundError) as exc_info:
                await service.ingest(ingest_request)

            assert exc_info.value.source_type == "unknown_type"


# =============================================================================
# 5. 통합 테스트
# =============================================================================


class TestIngestIntegration:
    """인덱싱 전체 파이프라인 통합 테스트"""

    @pytest.mark.anyio
    async def test_full_ingest_pipeline(self) -> None:
        """전체 인덱싱 파이프라인 테스트"""
        captured_requests: list[Dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured_requests.append({
                "url": str(request.url),
                "payload": payload,
            })
            return httpx.Response(
                status_code=200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": f"task-{payload['doc_id']}",
                        "status": "DONE",
                    },
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            ragflow = RagflowClient(base_url="http://test-ragflow:8080", client=client)
            service = IngestService(ragflow_client=ragflow)

            # 여러 문서 인덱싱
            for i, source_type in enumerate(["policy", "incident", "education"]):
                request = IngestRequest(
                    doc_id=f"DOC-{i:03d}",
                    source_type=source_type,
                    storage_url=f"https://files.internal/doc-{i}.pdf",
                    file_name=f"document-{i}.pdf",
                    mime_type="application/pdf",
                )

                response = await service.ingest(request)
                assert response.status == "DONE"
                assert response.task_id == f"task-DOC-{i:03d}"

        # 3개의 요청이 전송되었는지 확인
        assert len(captured_requests) == 3

        # 각 요청의 kb_id가 올바른지 확인
        expected_kb_ids = ["kb_policy_001", "kb_incident_001", "kb_education_001"]
        for i, req in enumerate(captured_requests):
            assert req["payload"]["kb_id"] == expected_kb_ids[i]
            assert "/v1/chunk/ingest" in req["url"]

    @pytest.mark.anyio
    async def test_ingest_with_metadata(self) -> None:
        """메타데이터 포함 인덱싱 테스트"""
        captured_payload: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_payload.update(json.loads(request.content))
            return httpx.Response(
                status_code=200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "task-metadata-001",
                        "status": "DONE",
                    },
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            ragflow = RagflowClient(base_url="http://test:8080", client=client)
            service = IngestService(ragflow_client=ragflow)

            request = IngestRequest(
                doc_id="DOC-META-001",
                source_type="policy",
                storage_url="https://files.internal/meta-doc.pdf",
                file_name="정보보안규정.pdf",
                mime_type="application/pdf",
                department="보안팀",
                acl=["ROLE_ADMIN", "ROLE_SECURITY"],
                tags=["보안", "필수교육", "연간"],
                version=5,
            )

            response = await service.ingest(request)

        assert response.status == "DONE"

        # 메타데이터 검증
        metadata = captured_payload["metadata"]
        assert metadata["department"] == "보안팀"
        assert metadata["acl"] == ["ROLE_ADMIN", "ROLE_SECURITY"]
        assert metadata["tags"] == ["보안", "필수교육", "연간"]
        assert metadata["version"] == 5
