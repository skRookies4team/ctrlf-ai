"""
Step 3: RagflowClient 문서 관리 메서드 단위 테스트

테스트 케이스:
1. get_document_status: 성공, 문서 없음, 에러
2. get_document_chunks: 성공, 페이지네이션, 에러
3. trigger_parsing: 성공, 에러
4. upload_document: 성공, 에러
"""

import pytest
import httpx
from unittest.mock import patch, MagicMock, AsyncMock

from app.clients.ragflow_client import (
    RagflowClient,
    RagflowError,
    RagflowConnectionError,
    clear_ragflow_client,
)
from app.core.config import clear_settings_cache


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_caches():
    """테스트 격리를 위해 캐시 초기화"""
    clear_settings_cache()
    clear_ragflow_client()
    yield
    clear_settings_cache()
    clear_ragflow_client()


@pytest.fixture
def mock_settings():
    """테스트용 설정 mock"""
    settings = MagicMock()
    settings.ragflow_base_url = "http://ragflow-test:8080"
    settings.RAGFLOW_API_KEY = "test-api-key"
    settings.ragflow_dataset_to_kb_mapping = {"education": "kb_edu_001"}
    return settings


@pytest.fixture
def mock_http_client():
    """테스트용 HTTP 클라이언트 mock"""
    return AsyncMock(spec=httpx.AsyncClient)


def create_client_with_mocks(mock_settings, mock_http_client):
    """Mock이 적용된 RagflowClient 생성"""
    with patch("app.clients.ragflow_client.get_settings", return_value=mock_settings):
        with patch("app.clients.ragflow_client.get_async_http_client", return_value=mock_http_client):
            return RagflowClient()


# =============================================================================
# 테스트: get_document_status
# =============================================================================


class TestGetDocumentStatus:
    """get_document_status 메서드 테스트"""

    @pytest.mark.anyio
    async def test_get_document_status_success(self, mock_settings, mock_http_client):
        """문서 상태 조회 성공"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "data": {
                "docs": [
                    {
                        "id": "doc-001",
                        "name": "test.pdf",
                        "run": "DONE",
                        "progress": 1.0,
                        "chunk_count": 10,
                        "token_count": 5000,
                    }
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.get.return_value = mock_response

        # Act
        result = await client.get_document_status(
            dataset_id="ds-001",
            document_id="doc-001",
        )

        # Assert
        assert result["id"] == "doc-001"
        assert result["run"] == "DONE"
        assert result["chunk_count"] == 10
        mock_http_client.get.assert_called_once()

    @pytest.mark.anyio
    async def test_get_document_status_not_found(self, mock_settings, mock_http_client):
        """문서 없음"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "data": {"docs": []}  # 빈 배열
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.get.return_value = mock_response

        # Act & Assert
        with pytest.raises(RagflowError, match="Document not found"):
            await client.get_document_status(
                dataset_id="ds-001",
                document_id="nonexistent",
            )

    @pytest.mark.anyio
    async def test_get_document_status_api_error(self, mock_settings, mock_http_client):
        """API 에러 응답"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 100,
            "message": "Invalid dataset ID"
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.get.return_value = mock_response

        # Act & Assert
        with pytest.raises(RagflowError, match="Invalid dataset ID"):
            await client.get_document_status(
                dataset_id="invalid-ds",
                document_id="doc-001",
            )

    @pytest.mark.anyio
    async def test_get_document_status_http_error(self, mock_settings, mock_http_client):
        """HTTP 에러"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_http_client.get.return_value = mock_response
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 error",
            request=MagicMock(),
            response=mock_response,
        )

        # Act & Assert
        with pytest.raises(RagflowError, match="HTTP 500"):
            await client.get_document_status(
                dataset_id="ds-001",
                document_id="doc-001",
            )

    @pytest.mark.anyio
    async def test_get_document_status_connection_error(self, mock_settings, mock_http_client):
        """연결 에러"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_http_client.get.side_effect = httpx.ConnectError("Connection refused")

        # Act & Assert
        with pytest.raises(RagflowConnectionError, match="Connection failed"):
            await client.get_document_status(
                dataset_id="ds-001",
                document_id="doc-001",
            )

    @pytest.mark.anyio
    async def test_get_document_status_no_base_url(self, mock_http_client):
        """BASE_URL이 설정되지 않은 경우"""
        # Arrange
        mock_settings = MagicMock()
        mock_settings.ragflow_base_url = None  # URL 미설정
        mock_settings.RAGFLOW_API_KEY = None
        mock_settings.ragflow_dataset_to_kb_mapping = {}

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act & Assert
        with pytest.raises(RagflowError, match="not configured"):
            await client.get_document_status(
                dataset_id="ds-001",
                document_id="doc-001",
            )


# =============================================================================
# 테스트: get_document_chunks
# =============================================================================


class TestGetDocumentChunks:
    """get_document_chunks 메서드 테스트"""

    @pytest.mark.anyio
    async def test_get_document_chunks_success(self, mock_settings, mock_http_client):
        """청크 조회 성공"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "data": {
                "total": 2,
                "chunks": [
                    {
                        "id": "chunk-001",
                        "content": "청크 내용 1",
                        "positions": [[1, 100, 100, 500, 200]],
                        "important_keywords": ["키워드1"],
                    },
                    {
                        "id": "chunk-002",
                        "content": "청크 내용 2",
                        "positions": [[1, 100, 300, 500, 400]],
                        "important_keywords": ["키워드2"],
                    },
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.get.return_value = mock_response

        # Act
        result = await client.get_document_chunks(
            dataset_id="ds-001",
            document_id="doc-001",
            page=1,
            page_size=100,
        )

        # Assert
        assert result["total"] == 2
        assert len(result["chunks"]) == 2
        assert result["chunks"][0]["id"] == "chunk-001"

    @pytest.mark.anyio
    async def test_get_document_chunks_with_pagination(self, mock_settings, mock_http_client):
        """페이지네이션 파라미터 전달 확인"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "data": {"total": 100, "chunks": []}
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.get.return_value = mock_response

        # Act
        await client.get_document_chunks(
            dataset_id="ds-001",
            document_id="doc-001",
            page=3,
            page_size=50,
        )

        # Assert
        call_args = mock_http_client.get.call_args
        assert call_args.kwargs["params"]["page"] == 3
        assert call_args.kwargs["params"]["page_size"] == 50

    @pytest.mark.anyio
    async def test_get_document_chunks_empty(self, mock_settings, mock_http_client):
        """빈 청크 응답"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "data": {"total": 0, "chunks": []}
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.get.return_value = mock_response

        # Act
        result = await client.get_document_chunks(
            dataset_id="ds-001",
            document_id="doc-001",
        )

        # Assert
        assert result["total"] == 0
        assert result["chunks"] == []


# =============================================================================
# 테스트: trigger_parsing
# =============================================================================


class TestTriggerParsing:
    """trigger_parsing 메서드 테스트"""

    @pytest.mark.anyio
    async def test_trigger_parsing_success(self, mock_settings, mock_http_client):
        """파싱 트리거 성공"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0, "data": True}
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_response

        # Act
        result = await client.trigger_parsing(
            dataset_id="ds-001",
            document_ids=["doc-001", "doc-002"],
        )

        # Assert
        assert result is True
        call_args = mock_http_client.post.call_args
        assert call_args.kwargs["json"]["document_ids"] == ["doc-001", "doc-002"]

    @pytest.mark.anyio
    async def test_trigger_parsing_api_error(self, mock_settings, mock_http_client):
        """파싱 트리거 API 에러"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 100,
            "message": "Documents already parsing"
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_response

        # Act & Assert
        with pytest.raises(RagflowError, match="already parsing"):
            await client.trigger_parsing(
                dataset_id="ds-001",
                document_ids=["doc-001"],
            )

    @pytest.mark.anyio
    async def test_trigger_parsing_connection_error(self, mock_settings, mock_http_client):
        """파싱 트리거 연결 에러"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_http_client.post.side_effect = httpx.ConnectError("Connection refused")

        # Act & Assert
        with pytest.raises(RagflowConnectionError):
            await client.trigger_parsing(
                dataset_id="ds-001",
                document_ids=["doc-001"],
            )


# =============================================================================
# 테스트: upload_document
# =============================================================================


class TestUploadDocument:
    """upload_document 메서드 테스트"""

    @pytest.mark.anyio
    async def test_upload_document_success(self, mock_settings, mock_http_client):
        """문서 업로드 성공"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        # 파일 다운로드 mock
        file_content = b"PDF file content"

        mock_download_response = MagicMock()
        mock_download_response.status_code = 200
        mock_download_response.content = file_content
        mock_download_response.raise_for_status = MagicMock()

        # 업로드 응답 mock
        mock_upload_response = MagicMock()
        mock_upload_response.status_code = 200
        mock_upload_response.json.return_value = {
            "code": 0,
            "data": [
                {"id": "ragflow-doc-001", "name": "test.pdf"}
            ]
        }
        mock_upload_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_upload_response

        # httpx.AsyncClient mock for file download
        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_download_client = AsyncMock()
            mock_download_client.get.return_value = mock_download_response
            MockAsyncClient.return_value.__aenter__.return_value = mock_download_client

            # Act
            result = await client.upload_document(
                dataset_id="ds-001",
                file_url="https://storage.example.com/test.pdf",
                file_name="test.pdf",
            )

        # Assert
        assert result["id"] == "ragflow-doc-001"
        assert result["name"] == "test.pdf"

    @pytest.mark.anyio
    async def test_upload_document_no_base_url(self, mock_http_client):
        """BASE_URL이 설정되지 않은 경우"""
        # Arrange
        mock_settings = MagicMock()
        mock_settings.ragflow_base_url = None
        mock_settings.RAGFLOW_API_KEY = None
        mock_settings.ragflow_dataset_to_kb_mapping = {}

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act & Assert
        with pytest.raises(RagflowError, match="not configured"):
            await client.upload_document(
                dataset_id="ds-001",
                file_url="https://example.com/test.pdf",
                file_name="test.pdf",
            )

    @pytest.mark.anyio
    async def test_upload_document_api_error(self, mock_settings, mock_http_client):
        """업로드 API 에러"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_download_response = MagicMock()
        mock_download_response.status_code = 200
        mock_download_response.content = b"content"
        mock_download_response.raise_for_status = MagicMock()

        mock_upload_response = MagicMock()
        mock_upload_response.status_code = 200
        mock_upload_response.json.return_value = {
            "code": 100,
            "message": "File type not supported"
        }
        mock_upload_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_upload_response

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_download_client = AsyncMock()
            mock_download_client.get.return_value = mock_download_response
            MockAsyncClient.return_value.__aenter__.return_value = mock_download_client

            # Act & Assert
            with pytest.raises(RagflowError, match="not supported"):
                await client.upload_document(
                    dataset_id="ds-001",
                    file_url="https://example.com/test.exe",
                    file_name="test.exe",
                )

    @pytest.mark.anyio
    async def test_upload_document_empty_response(self, mock_settings, mock_http_client):
        """업로드 성공했지만 문서 정보가 없는 경우"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        mock_download_response = MagicMock()
        mock_download_response.status_code = 200
        mock_download_response.content = b"content"
        mock_download_response.raise_for_status = MagicMock()

        mock_upload_response = MagicMock()
        mock_upload_response.status_code = 200
        mock_upload_response.json.return_value = {
            "code": 0,
            "data": []  # 빈 배열
        }
        mock_upload_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_upload_response

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_download_client = AsyncMock()
            mock_download_client.get.return_value = mock_download_response
            MockAsyncClient.return_value.__aenter__.return_value = mock_download_client

            # Act & Assert
            with pytest.raises(RagflowError, match="no document info"):
                await client.upload_document(
                    dataset_id="ds-001",
                    file_url="https://example.com/test.pdf",
                    file_name="test.pdf",
                )
