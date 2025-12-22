"""
Step 3: SourceSet Orchestrator 단위 테스트

테스트 케이스:
1. Polling 완료 케이스 (2-3번 폴링 후 DONE)
2. Polling 실패 케이스 (RAGFlow FAIL 응답)
3. Polling 타임아웃 케이스
4. 스크립트 생성 (LLM 연동)
5. sourceRefs 매핑
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from app.services.source_set_orchestrator import (
    SourceSetOrchestrator,
    ProcessingJob,
    ProcessingStatus,
    DocumentProcessingResult,
)
from app.models.source_set import (
    SourceSetDocument,
    SourceSetStartRequest,
    GeneratedScript,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture
def mock_backend_client():
    """Mock BackendClient"""
    client = MagicMock()
    client.get_source_set_documents = AsyncMock()
    client.bulk_upsert_chunks = AsyncMock()
    client.notify_source_set_complete = AsyncMock()
    return client


@pytest.fixture
def mock_ragflow_client():
    """Mock RagflowClient"""
    client = MagicMock()
    client._dataset_to_kb_id = MagicMock(return_value="kb_education_001")
    client.upload_document = AsyncMock()
    client.trigger_parsing = AsyncMock()
    client.get_document_status = AsyncMock()
    client.get_document_chunks = AsyncMock()
    return client


@pytest.fixture
def mock_llm_client():
    """Mock LLMClient"""
    client = MagicMock()
    client.generate_chat_completion = AsyncMock()
    return client


@pytest.fixture
def mock_settings():
    """Mock settings"""
    settings = MagicMock()
    settings.RAGFLOW_POLL_INTERVAL_SEC = 0.01  # 테스트 속도를 위해 짧게 설정
    settings.RAGFLOW_POLL_TIMEOUT_SEC = 0.1
    settings.RAGFLOW_CHUNK_PAGE_SIZE = 100
    return settings


@pytest.fixture
def sample_document():
    """샘플 문서"""
    return SourceSetDocument(
        document_id="doc-001",
        source_url="https://example.com/file.pdf",
        title="테스트 문서",
        domain="EDUCATION",
    )


@pytest.fixture
def sample_job(sample_document):
    """샘플 처리 작업"""
    return ProcessingJob(
        source_set_id="ss-001",
        video_id="video-001",
        education_id="edu-001",
        request_id="req-001",
        trace_id="trace-001",
        script_policy_id=None,
        llm_model_hint="qwen2.5-14b-instruct",
        status=ProcessingStatus.PROCESSING,
        documents=[sample_document],
    )


@pytest.fixture
def sample_chunks():
    """샘플 청크 데이터"""
    return [
        {
            "id": "chunk-001",
            "content": "법정의무교육의 개요입니다.",
            "positions": [[1, 100, 100, 500, 200]],
            "important_keywords": ["법정의무교육", "개요"],
            "questions": ["법정의무교육이란?"],
        },
        {
            "id": "chunk-002",
            "content": "성희롱 예방교육은 연 1회 이상 실시해야 합니다.",
            "positions": [[1, 100, 300, 500, 400]],
            "important_keywords": ["성희롱", "예방교육"],
            "questions": ["성희롱 예방교육 빈도는?"],
        },
    ]


# =============================================================================
# 테스트: Polling 로직
# =============================================================================


class TestPollingLogic:
    """Polling 로직 테스트"""

    @pytest.mark.anyio
    async def test_poll_completes_after_multiple_iterations(
        self, mock_backend_client, mock_ragflow_client, mock_settings
    ):
        """Polling이 2-3번 후 DONE으로 완료되는 케이스"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        # 첫 번째: RUNNING, 두 번째: RUNNING, 세 번째: DONE
        mock_ragflow_client.get_document_status.side_effect = [
            {"run": "RUNNING", "progress": 0.3, "chunk_count": 0},
            {"run": "RUNNING", "progress": 0.7, "chunk_count": 1},
            {"run": "DONE", "progress": 1.0, "chunk_count": 2},
        ]

        # Act - polling 파라미터를 직접 전달하므로 settings 패치 불필요
        status, chunk_count = await orchestrator._poll_document_status(
            dataset_id="ds-001",
            document_id="doc-001",
            poll_interval=0.01,
            timeout=1.0,
        )

        # Assert
        assert status == "DONE"
        assert chunk_count == 2
        assert mock_ragflow_client.get_document_status.call_count == 3

    @pytest.mark.anyio
    async def test_poll_returns_fail_status(
        self, mock_backend_client, mock_ragflow_client, mock_settings
    ):
        """RAGFlow가 FAIL을 반환하는 케이스"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        mock_ragflow_client.get_document_status.side_effect = [
            {"run": "RUNNING", "progress": 0.5, "chunk_count": 0},
            {"run": "FAIL", "progress": 0.5, "chunk_count": 0},
        ]

        # Act
        status, chunk_count = await orchestrator._poll_document_status(
            dataset_id="ds-001",
            document_id="doc-001",
            poll_interval=0.01,
            timeout=1.0,
        )

        # Assert
        assert status == "FAIL"
        assert chunk_count == 0

    @pytest.mark.anyio
    async def test_poll_returns_cancel_status(
        self, mock_backend_client, mock_ragflow_client, mock_settings
    ):
        """RAGFlow가 CANCEL을 반환하는 케이스"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        mock_ragflow_client.get_document_status.return_value = {
            "run": "CANCEL",
            "progress": 0.0,
            "chunk_count": 0,
        }

        # Act
        status, chunk_count = await orchestrator._poll_document_status(
            dataset_id="ds-001",
            document_id="doc-001",
            poll_interval=0.01,
            timeout=1.0,
        )

        # Assert
        assert status == "CANCEL"

    @pytest.mark.anyio
    async def test_poll_timeout(
        self, mock_backend_client, mock_ragflow_client, mock_settings
    ):
        """Polling 타임아웃 케이스"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        # 항상 RUNNING 반환
        mock_ragflow_client.get_document_status.return_value = {
            "run": "RUNNING",
            "progress": 0.5,
            "chunk_count": 0,
        }

        # Act - 짧은 타임아웃으로 테스트
        status, chunk_count = await orchestrator._poll_document_status(
            dataset_id="ds-001",
            document_id="doc-001",
            poll_interval=0.02,
            timeout=0.05,  # 50ms 타임아웃
        )

        # Assert
        assert status == "TIMEOUT"
        assert chunk_count == 0

    @pytest.mark.anyio
    async def test_poll_handles_api_errors_gracefully(
        self, mock_backend_client, mock_ragflow_client, mock_settings
    ):
        """API 에러 발생 시 재시도 후 성공하는 케이스"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        # 첫 번째: 에러, 두 번째: DONE
        mock_ragflow_client.get_document_status.side_effect = [
            Exception("Temporary error"),
            {"run": "DONE", "progress": 1.0, "chunk_count": 5},
        ]

        # Act
        status, chunk_count = await orchestrator._poll_document_status(
            dataset_id="ds-001",
            document_id="doc-001",
            poll_interval=0.01,
            timeout=1.0,
        )

        # Assert
        assert status == "DONE"
        assert chunk_count == 5


# =============================================================================
# 테스트: 청크 조회
# =============================================================================


class TestChunkFetching:
    """청크 조회 테스트"""

    @pytest.mark.anyio
    async def test_fetch_all_chunks_single_page(
        self, mock_backend_client, mock_ragflow_client, sample_chunks
    ):
        """단일 페이지 청크 조회"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        mock_ragflow_client.get_document_chunks.return_value = {
            "total": 2,
            "chunks": sample_chunks,
        }

        # Act
        chunks = await orchestrator._fetch_all_chunks(
            dataset_id="ds-001",
            document_id="doc-001",
            page_size=100,
        )

        # Assert
        assert len(chunks) == 2
        assert chunks[0]["chunk_index"] == 0
        assert chunks[1]["chunk_index"] == 1
        assert "chunk_text" in chunks[0]
        assert "chunk_meta" in chunks[0]
        assert chunks[0]["chunk_meta"]["ragflow_chunk_id"] == "chunk-001"

    @pytest.mark.anyio
    async def test_fetch_all_chunks_pagination(
        self, mock_backend_client, mock_ragflow_client
    ):
        """페이지네이션 청크 조회"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        # 첫 페이지: 2개, 두 번째 페이지: 1개, 세 번째 페이지: 빈 배열
        mock_ragflow_client.get_document_chunks.side_effect = [
            {"total": 3, "chunks": [
                {"id": "c1", "content": "Content 1"},
                {"id": "c2", "content": "Content 2"},
            ]},
            {"total": 3, "chunks": [
                {"id": "c3", "content": "Content 3"},
            ]},
            {"total": 3, "chunks": []},
        ]

        # Act
        chunks = await orchestrator._fetch_all_chunks(
            dataset_id="ds-001",
            document_id="doc-001",
            page_size=2,
        )

        # Assert
        assert len(chunks) == 3
        assert chunks[0]["chunk_index"] == 0
        assert chunks[2]["chunk_index"] == 2

    @pytest.mark.anyio
    async def test_fetch_all_chunks_empty(
        self, mock_backend_client, mock_ragflow_client
    ):
        """빈 청크 조회"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        mock_ragflow_client.get_document_chunks.return_value = {
            "total": 0,
            "chunks": [],
        }

        # Act
        chunks = await orchestrator._fetch_all_chunks(
            dataset_id="ds-001",
            document_id="doc-001",
        )

        # Assert
        assert len(chunks) == 0


# =============================================================================
# 테스트: 스크립트 생성
# =============================================================================


class TestScriptGeneration:
    """스크립트 생성 테스트"""

    @pytest.mark.anyio
    async def test_generate_script_with_llm(
        self, mock_backend_client, mock_ragflow_client, sample_job, mock_llm_client
    ):
        """LLM을 통한 스크립트 생성"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        document_chunks = {
            "doc-001": [
                {"chunk_index": 0, "chunk_text": "법정의무교육 개요"},
                {"chunk_index": 1, "chunk_text": "성희롱 예방교육 내용"},
            ]
        }

        # LLM 응답 mock
        llm_response = '''```json
{
    "title": "법정의무교육 안내",
    "chapters": [
        {
            "chapter_index": 1,
            "title": "개요",
            "scenes": [
                {
                    "scene_index": 1,
                    "purpose": "도입",
                    "narration": "법정의무교육에 대해 알아보겠습니다.",
                    "caption": "법정의무교육",
                    "visual": "타이틀 슬라이드",
                    "duration_sec": 15,
                    "source_chunk_indexes": [0]
                },
                {
                    "scene_index": 2,
                    "purpose": "설명",
                    "narration": "성희롱 예방교육은 연 1회 필수입니다.",
                    "caption": "성희롱 예방교육",
                    "visual": "인포그래픽",
                    "duration_sec": 20,
                    "source_chunk_indexes": [1]
                }
            ]
        }
    ]
}
```'''

        with patch("app.clients.llm_client.LLMClient") as MockLLMClient:
            mock_client_instance = MockLLMClient.return_value
            mock_client_instance.generate_chat_completion = AsyncMock(return_value=llm_response)

            # Act
            script = await orchestrator._generate_script(sample_job, document_chunks)

        # Assert
        assert isinstance(script, GeneratedScript)
        assert script.title == "법정의무교육 안내"
        assert len(script.chapters) == 1
        assert len(script.chapters[0].scenes) == 2
        assert script.chapters[0].scenes[0].narration == "법정의무교육에 대해 알아보겠습니다."

    @pytest.mark.anyio
    async def test_source_refs_mapping(
        self, mock_backend_client, mock_ragflow_client, sample_job
    ):
        """sourceRefs 매핑 테스트 - source_chunk_indexes → (documentId, chunkIndex)"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        # 두 문서에서 청크 수집
        document_chunks = {
            "doc-001": [
                {"chunk_index": 0, "chunk_text": "문서1 청크0"},
                {"chunk_index": 1, "chunk_text": "문서1 청크1"},
            ],
            "doc-002": [
                {"chunk_index": 0, "chunk_text": "문서2 청크0"},
            ],
        }

        # 두 번째 문서 추가
        sample_job.documents.append(SourceSetDocument(
            document_id="doc-002",
            source_url="https://example.com/file2.pdf",
            title="테스트 문서 2",
            domain="EDUCATION",
        ))

        # LLM 응답: source_chunk_indexes [0, 2]는 doc-001:0과 doc-002:0을 참조
        llm_response = '''```json
{
    "title": "테스트",
    "chapters": [{
        "chapter_index": 1,
        "title": "챕터1",
        "scenes": [{
            "scene_index": 1,
            "purpose": "설명",
            "narration": "내용",
            "caption": "자막",
            "visual": "시각",
            "duration_sec": 15,
            "source_chunk_indexes": [0, 2]
        }]
    }]
}
```'''

        with patch("app.clients.llm_client.LLMClient") as MockLLMClient:
            mock_client_instance = MockLLMClient.return_value
            mock_client_instance.generate_chat_completion = AsyncMock(return_value=llm_response)

            # Act
            script = await orchestrator._generate_script(sample_job, document_chunks)

        # Assert
        scene = script.chapters[0].scenes[0]
        assert len(scene.source_refs) == 2
        # 첫 번째 참조: doc-001:0
        assert scene.source_refs[0].document_id == "doc-001"
        assert scene.source_refs[0].chunk_index == 0
        # 두 번째 참조: doc-002:0 (전체 인덱스 2 → doc-002의 chunk_index 0)
        assert scene.source_refs[1].document_id == "doc-002"
        assert scene.source_refs[1].chunk_index == 0

    @pytest.mark.anyio
    async def test_generate_fallback_script_on_llm_failure(
        self, mock_backend_client, mock_ragflow_client, sample_job
    ):
        """LLM 실패 시 폴백 스크립트 생성"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        document_chunks = {
            "doc-001": [
                {"chunk_index": 0, "chunk_text": "테스트 청크 내용"},
            ]
        }

        with patch("app.clients.llm_client.LLMClient") as MockLLMClient:
            mock_client_instance = MockLLMClient.return_value
            mock_client_instance.generate_chat_completion = AsyncMock(
                side_effect=Exception("LLM error")
            )

            # Act
            script = await orchestrator._generate_script(sample_job, document_chunks)

        # Assert
        assert isinstance(script, GeneratedScript)
        assert "폴백" in script.title
        assert script.llm_model == "fallback"
        assert len(script.chapters) == 1

    @pytest.mark.anyio
    async def test_generate_fallback_on_invalid_json(
        self, mock_backend_client, mock_ragflow_client, sample_job
    ):
        """LLM이 잘못된 JSON을 반환할 때 폴백 스크립트 생성"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        document_chunks = {
            "doc-001": [
                {"chunk_index": 0, "chunk_text": "테스트 청크"},
            ]
        }

        with patch("app.clients.llm_client.LLMClient") as MockLLMClient:
            mock_client_instance = MockLLMClient.return_value
            mock_client_instance.generate_chat_completion = AsyncMock(
                return_value="This is not valid JSON"
            )

            # Act
            script = await orchestrator._generate_script(sample_job, document_chunks)

        # Assert
        assert isinstance(script, GeneratedScript)
        assert "폴백" in script.title


# =============================================================================
# 테스트: 문서 처리 통합
# =============================================================================


class TestDocumentProcessing:
    """문서 처리 통합 테스트"""

    @pytest.mark.anyio
    async def test_process_document_success(
        self, mock_backend_client, mock_ragflow_client, sample_job, sample_document, sample_chunks, mock_settings
    ):
        """문서 처리 성공 케이스"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        # Mock 설정
        mock_ragflow_client.upload_document.return_value = {"id": "ragflow-doc-001"}
        mock_ragflow_client.trigger_parsing.return_value = True
        mock_ragflow_client.get_document_status.return_value = {
            "run": "DONE",
            "progress": 1.0,
            "chunk_count": 2,
        }
        mock_ragflow_client.get_document_chunks.return_value = {
            "total": 2,
            "chunks": sample_chunks,
        }

        with patch("app.core.config.get_settings", return_value=mock_settings):
            # Act
            result = await orchestrator._process_document(
                source_set_id="ss-001",
                doc=sample_document,
                job=sample_job,
            )

        # Assert
        assert result.success is True
        assert result.chunks_count == 2
        assert len(result.chunks) == 2
        assert result.fail_reason is None

    @pytest.mark.anyio
    async def test_process_document_ragflow_fail(
        self, mock_backend_client, mock_ragflow_client, sample_job, sample_document, mock_settings
    ):
        """RAGFlow 파싱 실패 케이스"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        mock_ragflow_client.upload_document.return_value = {"id": "ragflow-doc-001"}
        mock_ragflow_client.trigger_parsing.return_value = True
        mock_ragflow_client.get_document_status.return_value = {
            "run": "FAIL",
            "progress": 0.5,
            "chunk_count": 0,
        }

        with patch("app.core.config.get_settings", return_value=mock_settings):
            # Act
            result = await orchestrator._process_document(
                source_set_id="ss-001",
                doc=sample_document,
                job=sample_job,
            )

        # Assert
        assert result.success is False
        assert "FAIL" in result.fail_reason

    @pytest.mark.anyio
    async def test_process_document_no_chunks(
        self, mock_backend_client, mock_ragflow_client, sample_job, sample_document, mock_settings
    ):
        """청크가 생성되지 않은 케이스"""
        # Arrange
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        mock_ragflow_client.upload_document.return_value = {"id": "ragflow-doc-001"}
        mock_ragflow_client.trigger_parsing.return_value = True
        mock_ragflow_client.get_document_status.return_value = {
            "run": "DONE",
            "progress": 1.0,
            "chunk_count": 0,
        }
        mock_ragflow_client.get_document_chunks.return_value = {
            "total": 0,
            "chunks": [],
        }

        with patch("app.core.config.get_settings", return_value=mock_settings):
            # Act
            result = await orchestrator._process_document(
                source_set_id="ss-001",
                doc=sample_document,
                job=sample_job,
            )

        # Assert
        assert result.success is False
        assert "no chunks" in result.fail_reason.lower()


# =============================================================================
# 테스트: JSON 파싱
# =============================================================================


class TestJsonParsing:
    """JSON 파싱 테스트"""

    def test_parse_json_with_code_block(self, mock_backend_client, mock_ragflow_client):
        """```json 블록이 있는 응답 파싱"""
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        response = '''Here is the script:
```json
{"title": "Test", "chapters": []}
```
That's the script.'''

        result = orchestrator._parse_script_json(response)

        assert result is not None
        assert result["title"] == "Test"

    def test_parse_json_without_code_block(self, mock_backend_client, mock_ragflow_client):
        """순수 JSON 응답 파싱"""
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        response = '{"title": "Direct JSON", "chapters": []}'

        result = orchestrator._parse_script_json(response)

        assert result is not None
        assert result["title"] == "Direct JSON"

    def test_parse_json_with_surrounding_text(self, mock_backend_client, mock_ragflow_client):
        """JSON 앞뒤에 텍스트가 있는 응답 파싱"""
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        response = 'Here is the result: {"title": "Extracted", "chapters": []} end.'

        result = orchestrator._parse_script_json(response)

        assert result is not None
        assert result["title"] == "Extracted"

    def test_parse_invalid_json_returns_none(self, mock_backend_client, mock_ragflow_client):
        """잘못된 JSON은 None 반환"""
        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend_client,
            ragflow_client=mock_ragflow_client,
        )

        response = 'This is not valid JSON at all'

        result = orchestrator._parse_script_json(response)

        assert result is None
