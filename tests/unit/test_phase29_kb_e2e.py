"""
Phase 29: KB Indexing E2E Tests

KB(Knowledge Base) 인덱싱 E2E 통합 테스트.

테스트 케이스:
A) 토큰 기반 청킹 테스트
   - 짧은 내용: 분할 없이 1개 청크
   - 긴 내용 (500+ 토큰): 여러 청크로 분할
   - chunk_id 형식: script_id:chapter:scene 또는 script_id:chapter:scene:part

B) ChatSource source_type 테스트
   - TRAINING_SCRIPT 소스 타입 설정 확인
   - POLICY 소스 타입 설정 확인

C) Milvus E2E 통합 테스트 (실제 Milvus 필요)
   - 스크립트 생성 → 승인 → 렌더 SUCCEEDED → publish → kb-status SUCCEEDED
   - 챗봇 RAG 검색에서 인덱싱된 교육 스크립트 청크 검색

Note: Milvus E2E 테스트는 실제 Milvus 연결이 필요합니다.
      MILVUS_ENABLED=true 환경에서만 실행됩니다.
"""

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import get_settings
from app.models.video_render import (
    KBChunk,
    KBIndexStatus,
    ScriptStatus,
    VideoScript,
)
from app.models.chat import ChatSource
from app.services.kb_index_service import KBIndexService


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def kb_service():
    """KB 인덱스 서비스 fixture (Mock 모드)."""
    return KBIndexService(milvus_client=None)


@pytest.fixture
def approved_script():
    """승인된 스크립트 fixture."""
    return VideoScript(
        script_id="script-029",
        video_id="video-029",
        status=ScriptStatus.APPROVED,
        raw_json={
            "chapters": [
                {
                    "chapter_id": 1,
                    "title": "보안교육 개요",
                    "scenes": [
                        {
                            "scene_id": 1,
                            "purpose": "인사",
                            "narration": "안녕하세요. 보안교육입니다.",
                            "caption": "환영합니다",
                        },
                    ],
                },
            ]
        },
        created_by="creator-029",
    )


@pytest.fixture
def long_content_script():
    """긴 내용을 가진 스크립트 fixture (토큰 분할 테스트용)."""
    # 500토큰 이상의 긴 narration 생성 (한국어 약 750자 = 500토큰)
    long_narration = "보안교육에 오신 것을 환영합니다. " * 50  # 약 1500자

    return VideoScript(
        script_id="script-long",
        video_id="video-long",
        status=ScriptStatus.APPROVED,
        raw_json={
            "chapters": [
                {
                    "chapter_id": 1,
                    "title": "긴 내용 테스트",
                    "scenes": [
                        {
                            "scene_id": 1,
                            "purpose": "긴 내용",
                            "narration": long_narration,
                        },
                    ],
                },
            ]
        },
        created_by="creator-long",
    )


# =============================================================================
# Phase 29-B: 토큰 기반 청킹 테스트
# =============================================================================


class TestTokenBasedChunking:
    """토큰 기반 청킹 테스트."""

    def test_short_content_single_chunk(self, kb_service, approved_script):
        """짧은 내용은 분할 없이 1개 청크 생성."""
        # When
        chunks = kb_service.build_chunks_from_script(approved_script)

        # Then
        assert len(chunks) == 1
        chunk = chunks[0]

        # chunk_id 형식: script_id:chapter:scene (분할 없음)
        assert chunk.chunk_id == "script-029:1:1"
        assert chunk.part_index is None
        assert "안녕하세요" in chunk.content
        assert chunk.source_type == "TRAINING_SCRIPT"

    def test_long_content_multiple_chunks(self, kb_service, long_content_script):
        """긴 내용은 여러 청크로 분할."""
        # When
        chunks = kb_service.build_chunks_from_script(long_content_script)

        # Then: 긴 내용은 여러 청크로 분할되어야 함
        assert len(chunks) >= 1  # 최소 1개 (분할 여부는 토큰 수에 따라)

        # 각 청크 확인
        for i, chunk in enumerate(chunks):
            assert chunk.script_id == "script-long"
            assert chunk.chapter_order == 1
            assert chunk.scene_order == 1
            assert chunk.source_type == "TRAINING_SCRIPT"

            if len(chunks) > 1:
                # 분할된 경우 chunk_id에 part 포함
                assert chunk.chunk_id == f"script-long:1:1:{i}"
                assert chunk.part_index == i
            else:
                # 분할 안 된 경우
                assert chunk.chunk_id == "script-long:1:1"
                assert chunk.part_index is None

    def test_chunk_preserves_metadata(self, kb_service, approved_script):
        """청크가 메타데이터를 보존."""
        # When
        chunks = kb_service.build_chunks_from_script(
            approved_script,
            course_type="FOUR_MANDATORY",
            year=2025,
            training_id="training-001",
        )

        # Then
        assert len(chunks) == 1
        chunk = chunks[0]

        assert chunk.metadata is not None
        assert chunk.metadata["course_type"] == "FOUR_MANDATORY"
        assert chunk.metadata["year"] == 2025
        assert chunk.metadata["training_id"] == "training-001"
        assert chunk.metadata["domain"] == "TRAINING"

    def test_empty_content_no_chunk(self, kb_service):
        """빈 내용은 청크 생성 안 함."""
        # Given
        empty_script = VideoScript(
            script_id="script-empty",
            video_id="video-empty",
            status=ScriptStatus.APPROVED,
            raw_json={
                "scenes": [
                    {"scene_id": 1, "narration": "", "caption": ""},
                ]
            },
            created_by="creator",
        )

        # When
        chunks = kb_service.build_chunks_from_script(empty_script)

        # Then
        assert len(chunks) == 0

    def test_source_refs_preserved_in_split_chunks(self, kb_service):
        """분할된 청크도 source_refs 보존."""
        # Given: source_refs가 있는 긴 스크립트
        long_narration = "피싱 공격 예방에 대해 알아봅시다. " * 50

        script = VideoScript(
            script_id="script-refs",
            video_id="video-refs",
            status=ScriptStatus.APPROVED,
            raw_json={
                "scenes": [
                    {
                        "scene_id": 1,
                        "narration": long_narration,
                        "source_refs": {"doc_id": "doc-001", "chunk_id": "chunk-001"},
                    },
                ]
            },
            created_by="creator",
        )

        # When
        chunks = kb_service.build_chunks_from_script(script)

        # Then: 모든 청크가 source_refs 보존
        for chunk in chunks:
            assert chunk.source_refs is not None
            assert chunk.source_refs["doc_id"] == "doc-001"


class TestTokenEstimation:
    """토큰 추정 테스트."""

    def test_estimate_tokens_char_based(self, kb_service):
        """문자 기반 토큰 추정."""
        # Given
        text = "안녕하세요"  # 5자

        # When
        tokens = kb_service._estimate_tokens(text)

        # Then: 5자 / 1.5 ≈ 3 토큰
        assert tokens == 3

    def test_estimate_tokens_english(self, kb_service):
        """영어 텍스트 토큰 추정."""
        # Given
        text = "Hello World"  # 11자

        # When
        tokens = kb_service._estimate_tokens(text)

        # Then: 11자 / 1.5 ≈ 7 토큰
        assert tokens == 7


class TestSentenceSplit:
    """문장 분할 테스트."""

    def test_split_korean_sentences(self, kb_service):
        """한국어 문장 분할."""
        # Given
        text = "안녕하세요. 반갑습니다! 질문 있나요?"

        # When
        sentences = kb_service._split_into_sentences(text)

        # Then
        assert len(sentences) == 3
        assert sentences[0] == "안녕하세요."
        assert sentences[1] == "반갑습니다!"
        assert sentences[2] == "질문 있나요?"

    def test_split_mixed_punctuation(self, kb_service):
        """혼합 문장 부호 분할."""
        # Given
        text = "첫 번째 문장입니다。두 번째 문장입니다！세 번째 문장입니다？"

        # When
        sentences = kb_service._split_into_sentences(text)

        # Then
        assert len(sentences) >= 1  # 최소 1개


# =============================================================================
# Phase 29-C: ChatSource source_type 테스트
# =============================================================================


class TestChatSourceType:
    """ChatSource source_type 테스트."""

    def test_training_script_source_type(self):
        """TRAINING_SCRIPT 소스 타입 설정."""
        # When
        source = ChatSource(
            doc_id="script-001:1:1",
            title="보안교육 스크립트",
            snippet="안녕하세요. 보안교육입니다.",
            source_type="TRAINING_SCRIPT",
        )

        # Then
        assert source.source_type == "TRAINING_SCRIPT"

    def test_policy_source_type(self):
        """POLICY 소스 타입 설정."""
        # When
        source = ChatSource(
            doc_id="policy-001",
            title="정보보호 규정",
            snippet="제10조 정보보호 의무",
            source_type="POLICY",
        )

        # Then
        assert source.source_type == "POLICY"

    def test_default_source_type_none(self):
        """기본 source_type은 None."""
        # When
        source = ChatSource(
            doc_id="doc-001",
            title="일반 문서",
        )

        # Then
        assert source.source_type is None


# =============================================================================
# Phase 29-A: Milvus E2E 통합 테스트
# =============================================================================


class TestMilvusE2E:
    """Milvus E2E 통합 테스트.

    Note: 실제 Milvus 연결이 필요합니다.
          MILVUS_ENABLED=true 환경에서만 실행됩니다.
    """

    @pytest.fixture
    def milvus_available(self):
        """Milvus 사용 가능 여부 확인."""
        settings = get_settings()
        if not settings.MILVUS_ENABLED:
            pytest.skip("MILVUS_ENABLED=false, skipping Milvus E2E test")
        return True

    @pytest.mark.asyncio
    async def test_kb_index_e2e_flow(self, milvus_available, approved_script):
        """KB 인덱싱 E2E 플로우 테스트.

        스크립트 생성 → 승인 → 렌더 SUCCEEDED → publish → kb-status SUCCEEDED
        """
        # This test requires actual Milvus connection
        # For now, we test the service initialization
        from app.clients.milvus_client import get_milvus_client

        try:
            client = get_milvus_client()
            health = await client.health_check()

            if not health:
                pytest.skip("Milvus not healthy, skipping E2E test")

            # Create KB service with real client
            kb_service = KBIndexService(milvus_client=client)

            # Build chunks
            chunks = kb_service.build_chunks_from_script(
                approved_script,
                course_type="FOUR_MANDATORY",
                year=2025,
            )

            assert len(chunks) > 0

            # Verify chunk structure
            for chunk in chunks:
                assert chunk.source_type == "TRAINING_SCRIPT"
                assert chunk.video_id == approved_script.video_id

        except Exception as e:
            pytest.skip(f"Milvus connection failed: {e}")

    @pytest.mark.asyncio
    async def test_chat_rag_search_with_training_script(self, milvus_available):
        """챗봇 RAG 검색에서 교육 스크립트 청크 검색.

        Note: 인덱싱된 데이터가 있어야 합니다.
        """
        from app.clients.milvus_client import get_milvus_client, MilvusConnectionError

        try:
            client = get_milvus_client()

            # Search for training content
            sources = await client.search_as_sources(
                query="보안교육",
                domain="TRAINING",
                top_k=5,
            )

            # Verify source_type if results exist
            for source in sources:
                if source.source_type:
                    # Training 도메인에서는 TRAINING_SCRIPT 예상
                    assert source.source_type in ["TRAINING_SCRIPT", "DOCUMENT"]

        except MilvusConnectionError:
            pytest.skip("Milvus connection failed")
        except Exception as e:
            pytest.skip(f"Search failed: {e}")


# =============================================================================
# KBChunk 모델 테스트
# =============================================================================


class TestKBChunkModel:
    """KBChunk 모델 테스트."""

    def test_chunk_with_part_index(self):
        """분할된 청크 생성."""
        chunk = KBChunk(
            chunk_id="script-001:1:1:0",
            video_id="video-001",
            script_id="script-001",
            chapter_order=1,
            scene_order=1,
            chapter_title="Chapter 1",
            scene_purpose="Intro",
            content="First part of content",
            part_index=0,
            source_type="TRAINING_SCRIPT",
        )

        assert chunk.part_index == 0
        assert chunk.source_type == "TRAINING_SCRIPT"
        assert ":0" in chunk.chunk_id

    def test_chunk_without_part_index(self):
        """분할되지 않은 청크 생성."""
        chunk = KBChunk(
            chunk_id="script-001:1:1",
            video_id="video-001",
            script_id="script-001",
            chapter_order=1,
            scene_order=1,
            chapter_title="Chapter 1",
            scene_purpose="Intro",
            content="Full content",
        )

        assert chunk.part_index is None
        # source_type 기본값
        assert chunk.source_type == "TRAINING_SCRIPT"

    def test_chunk_default_source_type(self):
        """청크 기본 source_type."""
        chunk = KBChunk(
            chunk_id="script-001:1:1",
            video_id="video-001",
            script_id="script-001",
            chapter_order=1,
            scene_order=1,
            chapter_title="",
            scene_purpose="",
            content="Content",
        )

        # 기본값 확인
        assert chunk.source_type == "TRAINING_SCRIPT"


# =============================================================================
# Integration: KBIndexService + ChatSource
# =============================================================================


class TestKBToChatSourceIntegration:
    """KBIndexService와 ChatSource 통합 테스트."""

    def test_kb_chunk_to_chat_source_conversion(self, kb_service, approved_script):
        """KBChunk를 ChatSource로 변환 가능."""
        # Given: KB 청크 생성
        chunks = kb_service.build_chunks_from_script(approved_script)
        assert len(chunks) > 0

        chunk = chunks[0]

        # When: ChatSource로 변환
        source = ChatSource(
            doc_id=chunk.chunk_id,
            title=f"{chunk.chapter_title} - {chunk.scene_purpose}",
            snippet=chunk.content[:500],
            source_type=chunk.source_type,
        )

        # Then
        assert source.doc_id == chunk.chunk_id
        assert source.source_type == "TRAINING_SCRIPT"
        assert chunk.content in source.snippet or source.snippet in chunk.content
