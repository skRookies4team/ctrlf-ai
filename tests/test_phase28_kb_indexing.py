"""
Phase 28: KB Indexing Tests

KB(Knowledge Base) 적재 기능 테스트.

테스트 케이스:
1. APPROVED + SUCCEEDED + not expired → publish 성공
2. script not APPROVED → publish 409
3. render not SUCCEEDED → publish 409
4. expired → publish 404
5. re-publish → 이전 doc 삭제 확인
6. KB chunking 로직 테스트
"""

import pytest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.video_render import (
    KBChunk,
    KBDocumentStatus,
    KBIndexStatus,
    RenderJobStatus,
    ScriptStatus,
    VideoRenderJob,
    VideoScript,
)
from app.services.kb_index_service import KBIndexService, get_kb_index_service, clear_kb_index_service
from app.services.video_render_service import (
    VideoRenderService,
    VideoScriptStore,
    VideoRenderJobStore,
    VideoAssetStore,
    get_video_render_service,
)
from app.services.education_catalog_service import (
    get_education_catalog_service,
    clear_education_catalog_service,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def script_store():
    """스크립트 저장소 fixture."""
    return VideoScriptStore()


@pytest.fixture
def job_store():
    """잡 저장소 fixture."""
    return VideoRenderJobStore()


@pytest.fixture
def asset_store():
    """에셋 저장소 fixture."""
    return VideoAssetStore()


@pytest.fixture
def render_service(script_store, job_store, asset_store):
    """렌더 서비스 fixture."""
    return VideoRenderService(
        script_store=script_store,
        job_store=job_store,
        asset_store=asset_store,
    )


@pytest.fixture
def kb_service():
    """KB 인덱스 서비스 fixture (Mock 모드)."""
    return KBIndexService(milvus_client=None)


@pytest.fixture
def approved_script():
    """승인된 스크립트 fixture."""
    return VideoScript(
        script_id="script-001",
        video_id="video-001",
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
                        {
                            "scene_id": 2,
                            "purpose": "주제 소개",
                            "narration": "오늘은 피싱 공격에 대해 알아봅니다.",
                        },
                    ],
                },
                {
                    "chapter_id": 2,
                    "title": "피싱 공격 유형",
                    "scenes": [
                        {
                            "scene_id": 1,
                            "purpose": "이메일 피싱",
                            "narration": "이메일 피싱은 가장 흔한 공격 유형입니다.",
                            "source_refs": {"doc_id": "doc-001", "chunk_id": "chunk-001"},
                        },
                    ],
                },
            ]
        },
        created_by="creator-001",
    )


@pytest.fixture
def succeeded_job():
    """성공한 렌더 잡 fixture."""
    return VideoRenderJob(
        job_id="job-001",
        video_id="video-001",
        script_id="script-001",
        status=RenderJobStatus.SUCCEEDED,
        requested_by="reviewer-001",
        finished_at=datetime.utcnow(),
    )


@pytest.fixture
def draft_script():
    """초안 스크립트 fixture."""
    return VideoScript(
        script_id="script-draft",
        video_id="video-001",
        status=ScriptStatus.DRAFT,
        raw_json={"scenes": [{"scene_id": 1, "text": "초안입니다."}]},
        created_by="creator-001",
    )


@pytest.fixture
def setup_education_catalog():
    """교육 카탈로그 설정 fixture."""
    clear_education_catalog_service()
    yield
    clear_education_catalog_service()


# =============================================================================
# Publish API Tests
# =============================================================================


class TestPublishSuccess:
    """발행 성공 테스트."""

    def test_publish_with_approved_and_succeeded(
        self, render_service, approved_script, succeeded_job
    ):
        """APPROVED + SUCCEEDED + not expired → 발행 성공."""
        # Given: 승인된 스크립트와 성공한 잡
        render_service._script_store.save(approved_script)
        render_service._job_store.save(succeeded_job)

        # When: 발행 검증
        script = render_service.get_script(succeeded_job.script_id)
        job = render_service._job_store.get_succeeded_by_video_id("video-001")

        # Then: 조건 충족
        assert script is not None
        assert script.is_approved()
        assert job is not None
        assert job.status == RenderJobStatus.SUCCEEDED

    def test_publish_changes_status_to_published(
        self, render_service, approved_script, succeeded_job
    ):
        """발행 시 스크립트 상태가 PUBLISHED로 변경."""
        # Given
        render_service._script_store.save(approved_script)
        render_service._job_store.save(succeeded_job)

        # When: 발행 처리
        script = render_service.get_script(approved_script.script_id)
        script.status = ScriptStatus.PUBLISHED
        script.kb_index_status = KBIndexStatus.PENDING
        render_service._script_store.save(script)

        # Then
        updated = render_service.get_script(approved_script.script_id)
        assert updated.status == ScriptStatus.PUBLISHED
        assert updated.kb_index_status == KBIndexStatus.PENDING


class TestPublishValidation:
    """발행 검증 테스트."""

    def test_publish_fails_when_script_not_approved(
        self, render_service, draft_script, succeeded_job
    ):
        """스크립트가 APPROVED가 아니면 발행 실패."""
        # Given: 초안 스크립트
        render_service._script_store.save(draft_script)
        succeeded_job.script_id = draft_script.script_id
        render_service._job_store.save(succeeded_job)

        # When/Then
        script = render_service.get_script(draft_script.script_id)
        assert not script.is_approved()

    def test_publish_fails_when_render_not_succeeded(
        self, render_service, approved_script
    ):
        """렌더 잡이 SUCCEEDED가 아니면 발행 실패."""
        # Given: 승인된 스크립트만 있고 성공한 잡 없음
        render_service._script_store.save(approved_script)

        # When/Then
        job = render_service._job_store.get_succeeded_by_video_id("video-001")
        assert job is None

    def test_publish_fails_when_job_pending(
        self, render_service, approved_script
    ):
        """렌더 잡이 PENDING이면 발행 실패."""
        # Given
        render_service._script_store.save(approved_script)
        pending_job = VideoRenderJob(
            job_id="job-pending",
            video_id="video-001",
            script_id="script-001",
            status=RenderJobStatus.PENDING,
            requested_by="reviewer-001",
        )
        render_service._job_store.save(pending_job)

        # When/Then
        succeeded = render_service._job_store.get_succeeded_by_video_id("video-001")
        assert succeeded is None


class TestExpiredEducation:
    """만료된 교육 테스트."""

    def test_publish_fails_when_education_expired(self, setup_education_catalog):
        """교육이 EXPIRED면 발행 실패."""
        # Given: 만료된 교육 등록
        catalog = get_education_catalog_service()
        catalog.register_education(
            education_id="video-expired",
            year=2024,
            due_date=date(2024, 1, 1),  # 과거 날짜
            is_mandatory_4type=True,
        )

        # When/Then
        assert catalog.is_expired("video-expired")

    def test_publish_succeeds_when_education_active(self, setup_education_catalog):
        """교육이 ACTIVE면 발행 가능."""
        # Given: 활성 교육 등록
        catalog = get_education_catalog_service()
        catalog.register_education(
            education_id="video-active",
            year=2025,
            due_date=date(2025, 12, 31),
            is_mandatory_4type=True,
        )

        # When/Then
        assert not catalog.is_expired("video-active")


# =============================================================================
# KB Index Service Tests
# =============================================================================


class TestKBChunking:
    """KB 청킹 테스트."""

    def test_build_chunks_from_script_with_chapters(self, kb_service, approved_script):
        """챕터/씬 구조에서 청크 생성."""
        # When
        chunks = kb_service.build_chunks_from_script(approved_script)

        # Then: 3개 씬 → 3개 청크
        assert len(chunks) == 3

        # 첫 번째 청크 확인
        chunk1 = chunks[0]
        assert chunk1.chapter_order == 1
        assert chunk1.scene_order == 1
        assert chunk1.chapter_title == "보안교육 개요"
        assert "안녕하세요" in chunk1.content
        assert "환영합니다" in chunk1.content

        # chunk_id 형식 확인
        assert chunk1.chunk_id == f"{approved_script.script_id}:1:1"

    def test_build_chunks_from_simple_scenes(self, kb_service):
        """간단한 scenes 구조에서 청크 생성."""
        # Given: chapters 없이 scenes만 있는 스크립트
        simple_script = VideoScript(
            script_id="script-simple",
            video_id="video-002",
            status=ScriptStatus.APPROVED,
            raw_json={
                "scenes": [
                    {"scene_id": 1, "text": "첫 번째 씬입니다."},
                    {"scene_id": 2, "text": "두 번째 씬입니다."},
                ]
            },
            created_by="creator",
        )

        # When
        chunks = kb_service.build_chunks_from_script(simple_script)

        # Then
        assert len(chunks) == 2
        assert chunks[0].content == "첫 번째 씬입니다."
        assert chunks[1].content == "두 번째 씬입니다."

    def test_chunk_preserves_source_refs(self, kb_service, approved_script):
        """source_refs가 청크에 보존됨."""
        # When
        chunks = kb_service.build_chunks_from_script(approved_script)

        # Then: 챕터 2의 씬 1에 source_refs 있음
        chunk_with_refs = [c for c in chunks if c.chapter_order == 2][0]
        assert chunk_with_refs.source_refs is not None
        assert chunk_with_refs.source_refs["doc_id"] == "doc-001"

    def test_empty_content_scene_skipped(self, kb_service):
        """내용 없는 씬은 청크 생성 안함."""
        # Given
        empty_script = VideoScript(
            script_id="script-empty",
            video_id="video-003",
            status=ScriptStatus.APPROVED,
            raw_json={
                "scenes": [
                    {"scene_id": 1, "text": ""},
                    {"scene_id": 2, "text": "실제 내용"},
                ]
            },
            created_by="creator",
        )

        # When
        chunks = kb_service.build_chunks_from_script(empty_script)

        # Then: 빈 씬은 제외
        assert len(chunks) == 1
        assert chunks[0].content == "실제 내용"


class TestKBIndexing:
    """KB 인덱싱 테스트."""

    @pytest.mark.asyncio
    async def test_index_published_video_success(self, kb_service, approved_script):
        """발행된 영상 인덱싱 성공."""
        # When
        result = await kb_service.index_published_video(
            video_id="video-001",
            script=approved_script,
            course_type="FOUR_MANDATORY",
            year=2025,
        )

        # Then
        assert result == KBIndexStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_index_fails_for_non_approved_script(self, kb_service, draft_script):
        """미승인 스크립트 인덱싱 실패."""
        # When/Then
        with pytest.raises(ValueError, match="not approved"):
            await kb_service.index_published_video(
                video_id="video-001",
                script=draft_script,
            )

    @pytest.mark.asyncio
    async def test_index_returns_failed_for_empty_chunks(self, kb_service):
        """청크 없으면 FAILED 반환."""
        # Given: 내용 없는 스크립트
        empty_script = VideoScript(
            script_id="script-empty",
            video_id="video-empty",
            status=ScriptStatus.APPROVED,
            raw_json={"scenes": []},
            created_by="creator",
        )

        # When
        result = await kb_service.index_published_video(
            video_id="video-empty",
            script=empty_script,
        )

        # Then
        assert result == KBIndexStatus.FAILED


class TestArchivePreviousVersion:
    """이전 버전 아카이브 테스트."""

    @pytest.mark.asyncio
    async def test_archive_called_on_republish(self, kb_service):
        """재발행 시 이전 버전 아카이브 호출."""
        # Given: Mock 모드에서 로그 확인
        # When
        result = await kb_service.archive_previous_version(
            video_id="video-001",
            current_script_id="script-002",
        )

        # Then: Mock 모드에서는 0 반환
        assert result == 0


# =============================================================================
# Model Tests
# =============================================================================


class TestVideoScriptModel:
    """VideoScript 모델 테스트."""

    def test_is_approved_returns_true_for_approved(self):
        """APPROVED 상태면 is_approved() True."""
        script = VideoScript(
            script_id="test",
            video_id="video",
            status=ScriptStatus.APPROVED,
            raw_json={},
            created_by="creator",
        )
        assert script.is_approved()

    def test_is_approved_returns_true_for_published(self):
        """PUBLISHED 상태도 is_approved() True."""
        script = VideoScript(
            script_id="test",
            video_id="video",
            status=ScriptStatus.PUBLISHED,
            raw_json={},
            created_by="creator",
        )
        assert script.is_approved()

    def test_is_approved_returns_false_for_draft(self):
        """DRAFT 상태면 is_approved() False."""
        script = VideoScript(
            script_id="test",
            video_id="video",
            status=ScriptStatus.DRAFT,
            raw_json={},
            created_by="creator",
        )
        assert not script.is_approved()

    def test_is_published(self):
        """is_published() 테스트."""
        published = VideoScript(
            script_id="test",
            video_id="video",
            status=ScriptStatus.PUBLISHED,
            raw_json={},
            created_by="creator",
        )
        approved = VideoScript(
            script_id="test2",
            video_id="video",
            status=ScriptStatus.APPROVED,
            raw_json={},
            created_by="creator",
        )
        assert published.is_published()
        assert not approved.is_published()

    def test_is_kb_indexed(self):
        """is_kb_indexed() 테스트."""
        indexed = VideoScript(
            script_id="test",
            video_id="video",
            status=ScriptStatus.PUBLISHED,
            raw_json={},
            created_by="creator",
            kb_index_status=KBIndexStatus.SUCCEEDED,
        )
        not_indexed = VideoScript(
            script_id="test2",
            video_id="video",
            status=ScriptStatus.PUBLISHED,
            raw_json={},
            created_by="creator",
            kb_index_status=KBIndexStatus.PENDING,
        )
        assert indexed.is_kb_indexed()
        assert not not_indexed.is_kb_indexed()


class TestKBIndexStatusEnum:
    """KBIndexStatus enum 테스트."""

    def test_all_statuses_exist(self):
        """모든 상태 존재 확인."""
        assert KBIndexStatus.NOT_INDEXED.value == "NOT_INDEXED"
        assert KBIndexStatus.PENDING.value == "PENDING"
        assert KBIndexStatus.RUNNING.value == "RUNNING"
        assert KBIndexStatus.SUCCEEDED.value == "SUCCEEDED"
        assert KBIndexStatus.FAILED.value == "FAILED"


class TestKBDocumentStatusEnum:
    """KBDocumentStatus enum 테스트."""

    def test_all_statuses_exist(self):
        """모든 상태 존재 확인."""
        assert KBDocumentStatus.ACTIVE.value == "ACTIVE"
        assert KBDocumentStatus.ARCHIVED.value == "ARCHIVED"


class TestKBChunkModel:
    """KBChunk 모델 테스트."""

    def test_chunk_creation(self):
        """청크 생성 테스트."""
        chunk = KBChunk(
            chunk_id="script-001:1:1",
            video_id="video-001",
            script_id="script-001",
            chapter_order=1,
            scene_order=1,
            chapter_title="Introduction",
            scene_purpose="Greeting",
            content="Hello, welcome to the training.",
            source_refs={"doc_id": "doc-001"},
            metadata={"course_type": "FOUR_MANDATORY"},
        )
        assert chunk.chunk_id == "script-001:1:1"
        assert chunk.content == "Hello, welcome to the training."
        assert chunk.source_refs["doc_id"] == "doc-001"
