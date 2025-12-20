"""
Phase 38: Script Snapshot on Job Start Tests

테스트 케이스:
1. start 호출 시 backend가 1회 호출되고 render_spec_json이 저장된다.
2. retry 호출 시 backend 호출 없이 저장된 render_spec_json을 그대로 쓴다.
3. backend 404/401/500 각각에서 job이 FAILED로 바뀌는지 확인
4. narration 빈 씬 처리(스킵/경고)가 파이프라인을 깨지 않는지
5. 이미 시작된 잡에 대한 start 호출은 idempotent (no-op)
"""

import json
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.backend_client import (
    BackendScriptClient,
    EmptyRenderSpecError,
    ScriptFetchError,
)
from app.models.render_spec import (
    RenderScene,
    RenderSpec,
    RenderSpecResponse,
    validate_render_spec,
)
from app.repositories.render_job_repository import (
    RenderJobEntity,
    RenderJobRepository,
    clear_render_job_repository,
)
from app.services.render_job_runner import (
    JobStartResult,
    RenderJobRunner,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_render_spec_response():
    """샘플 render-spec 응답 데이터."""
    return {
        "script_id": "script-001",
        "video_id": "video-001",
        "title": "테스트 교육 영상",
        "total_duration_sec": 60,
        "scenes": [
            {
                "scene_id": "scene-001",
                "scene_order": 1,
                "chapter_title": "소개",
                "purpose": "hook",
                "narration": "안녕하세요, 오늘은 보안에 대해 알아보겠습니다.",
                "caption": "보안 교육",
                "duration_sec": 15,
                "visual_spec": {
                    "type": "TEXT_HIGHLIGHT",
                    "text": "보안",
                    "highlight_terms": ["보안"]
                }
            },
            {
                "scene_id": "scene-002",
                "scene_order": 2,
                "chapter_title": "본론",
                "purpose": "explanation",
                "narration": "비밀번호는 주기적으로 변경해야 합니다.",
                "caption": "비밀번호 관리",
                "duration_sec": 20,
                "visual_spec": None
            },
            {
                "scene_id": "scene-003",
                "scene_order": 3,
                "chapter_title": "마무리",
                "purpose": "summary",
                "narration": "오늘 배운 내용을 정리하겠습니다.",
                "caption": "요약",
                "duration_sec": 25,
                "visual_spec": None
            },
        ]
    }


@pytest.fixture
def sample_render_spec(sample_render_spec_response):
    """샘플 RenderSpec 객체."""
    return RenderSpec(**sample_render_spec_response)


@pytest.fixture
def empty_render_spec_response():
    """빈 씬 목록 응답."""
    return {
        "script_id": "script-empty",
        "video_id": "video-empty",
        "title": "빈 영상",
        "total_duration_sec": 0,
        "scenes": []
    }


@pytest.fixture
def render_spec_with_empty_narration():
    """빈 narration을 가진 씬이 포함된 응답."""
    return {
        "script_id": "script-002",
        "video_id": "video-002",
        "title": "테스트 영상",
        "total_duration_sec": 30,
        "scenes": [
            {
                "scene_id": "scene-001",
                "scene_order": 1,
                "chapter_title": "챕터1",
                "purpose": "hook",
                "narration": "",  # 빈 narration
                "caption": "캡션만",
                "duration_sec": 15,
                "visual_spec": None
            },
            {
                "scene_id": "scene-002",
                "scene_order": 2,
                "chapter_title": "챕터2",
                "purpose": "explanation",
                "narration": "정상 나레이션",
                "caption": "캡션",
                "duration_sec": 0,  # duration 0
                "visual_spec": None
            },
        ]
    }


@pytest.fixture
def mock_repository(tmp_path):
    """테스트용 Repository."""
    clear_render_job_repository()
    db_path = str(tmp_path / "test_jobs.db")
    return RenderJobRepository(db_path=db_path)


# =============================================================================
# BackendScriptClient Tests
# =============================================================================


class TestBackendScriptClient:
    """BackendScriptClient 테스트."""

    @pytest.mark.asyncio
    async def test_get_render_spec_success(self, sample_render_spec_response):
        """정상적인 render-spec 조회."""
        # Mock HTTP 응답
        mock_response = httpx.Response(
            status_code=200,
            json=sample_render_spec_response,
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        client = BackendScriptClient(
            base_url="http://localhost:8080",
            token="test-token",
            timeout=10,
            client=mock_client,
        )

        spec = await client.get_render_spec("script-001")

        assert spec.script_id == "script-001"
        assert spec.video_id == "video-001"
        assert len(spec.scenes) == 3
        assert spec.scenes[0].narration.startswith("안녕하세요")

    @pytest.mark.asyncio
    async def test_get_render_spec_404(self):
        """스크립트 없음 (404)."""
        mock_response = httpx.Response(
            status_code=404,
            content=b"Not found",
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        client = BackendScriptClient(
            base_url="http://localhost:8080",
            token="test-token",
            client=mock_client,
        )

        with pytest.raises(ScriptFetchError) as exc_info:
            await client.get_render_spec("script-not-found")

        assert exc_info.value.error_code == "SCRIPT_NOT_FOUND"
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_render_spec_401(self):
        """인증 실패 (401)."""
        mock_response = httpx.Response(
            status_code=401,
            content=b"Unauthorized",
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        client = BackendScriptClient(
            base_url="http://localhost:8080",
            token="invalid-token",
            client=mock_client,
        )

        with pytest.raises(ScriptFetchError) as exc_info:
            await client.get_render_spec("script-001")

        assert exc_info.value.error_code == "SCRIPT_FETCH_UNAUTHORIZED"
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_render_spec_500(self):
        """서버 에러 (500)."""
        mock_response = httpx.Response(
            status_code=500,
            content=b"Internal Server Error",
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        client = BackendScriptClient(
            base_url="http://localhost:8080",
            token="test-token",
            client=mock_client,
        )

        with pytest.raises(ScriptFetchError) as exc_info:
            await client.get_render_spec("script-001")

        assert exc_info.value.error_code == "SCRIPT_FETCH_SERVER_ERROR"
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_get_render_spec_empty_scenes(self, empty_render_spec_response):
        """빈 씬 목록 조회 시 EmptyRenderSpecError."""
        mock_response = httpx.Response(
            status_code=200,
            json=empty_render_spec_response,
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        client = BackendScriptClient(
            base_url="http://localhost:8080",
            token="test-token",
            client=mock_client,
        )

        with pytest.raises(EmptyRenderSpecError) as exc_info:
            await client.get_render_spec("script-empty")

        assert exc_info.value.error_code == "EMPTY_RENDER_SPEC"

    @pytest.mark.asyncio
    async def test_get_render_spec_no_base_url(self):
        """BACKEND_BASE_URL 미설정 시 에러."""
        client = BackendScriptClient(
            base_url="",
            token="test-token",
        )

        with pytest.raises(ScriptFetchError) as exc_info:
            await client.get_render_spec("script-001")

        assert exc_info.value.error_code == "BACKEND_NOT_CONFIGURED"


# =============================================================================
# RenderSpec Validation Tests
# =============================================================================


class TestRenderSpecValidation:
    """RenderSpec 검증 테스트."""

    def test_validate_empty_narration(self, render_spec_with_empty_narration):
        """빈 narration 검증."""
        spec = RenderSpec(**render_spec_with_empty_narration)
        normalized, warnings = validate_render_spec(spec)

        # 빈 narration에 대한 경고가 있어야 함
        assert any("empty narration" in w for w in warnings)
        # duration 0 → 5초로 보정
        assert any("duration_sec" in w for w in warnings)

        # 정규화된 duration 확인
        scene_002 = next(s for s in normalized.scenes if s.scene_id == "scene-002")
        assert scene_002.duration_sec == 5.0  # 기본값으로 보정

    def test_validate_normal_spec(self, sample_render_spec):
        """정상 스펙 검증."""
        normalized, warnings = validate_render_spec(sample_render_spec)

        assert len(warnings) == 0
        assert normalized.get_scene_count() == 3

    def test_render_spec_to_raw_json(self, sample_render_spec):
        """RenderSpec → raw_json 변환."""
        raw_json = sample_render_spec.to_raw_json()

        assert "chapters" in raw_json
        assert "title" in raw_json
        assert len(raw_json["chapters"]) > 0


# =============================================================================
# RenderJobRunner.start_job Tests
# =============================================================================


class TestRenderJobRunnerStartJob:
    """RenderJobRunner.start_job 테스트."""

    @pytest.mark.asyncio
    async def test_start_job_fetches_render_spec(
        self, mock_repository, sample_render_spec_response
    ):
        """start_job 호출 시 backend에서 render-spec 조회."""
        # Job 생성
        job = RenderJobEntity(
            job_id="job-001",
            video_id="video-001",
            script_id="script-001",
            status="PENDING",
        )
        mock_repository.save(job)

        # Mock script client
        mock_client = AsyncMock()
        mock_client.get_render_spec = AsyncMock(
            return_value=RenderSpec(**sample_render_spec_response)
        )

        # Runner 생성
        runner = RenderJobRunner(
            repository=mock_repository,
            script_client=mock_client,
            renderer=MagicMock(),  # 렌더링은 스킵
        )

        # _execute_job_with_spec을 모킹하여 실제 렌더링 스킵
        runner._execute_job_with_spec = AsyncMock()

        result = await runner.start_job("job-001")

        # 검증
        assert result.started is True
        assert result.error_code is None
        mock_client.get_render_spec.assert_called_once_with("script-001")

        # render_spec_json이 저장되었는지 확인
        saved_job = mock_repository.get("job-001")
        assert saved_job.has_render_spec()
        assert saved_job.render_spec_json["script_id"] == "script-001"
        assert len(saved_job.render_spec_json["scenes"]) == 3

    @pytest.mark.asyncio
    async def test_retry_job_does_not_fetch_backend(
        self, mock_repository, sample_render_spec_response
    ):
        """retry_job 호출 시 backend 호출 없이 기존 스냅샷 사용."""
        # 이미 render_spec_json이 있는 FAILED Job 생성
        job = RenderJobEntity(
            job_id="job-002",
            video_id="video-002",
            script_id="script-002",
            status="FAILED",
            render_spec_json=sample_render_spec_response,
        )
        mock_repository.save(job)

        # Mock script client
        mock_client = AsyncMock()
        mock_client.get_render_spec = AsyncMock()  # 호출되면 안됨

        runner = RenderJobRunner(
            repository=mock_repository,
            script_client=mock_client,
            renderer=MagicMock(),
        )
        runner._execute_job_with_spec = AsyncMock()

        result = await runner.retry_job("job-002")

        # 검증
        assert result.started is True
        # Backend 호출이 없어야 함
        mock_client.get_render_spec.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_job_already_running(self, mock_repository, sample_render_spec_response):
        """이미 RUNNING 상태인 잡에 start 호출 시 no-op."""
        # RUNNING 상태의 Job 생성
        job = RenderJobEntity(
            job_id="job-003",
            video_id="video-003",
            script_id="script-003",
            status="RUNNING",
            render_spec_json=sample_render_spec_response,
        )
        mock_repository.save(job)

        mock_client = AsyncMock()

        runner = RenderJobRunner(
            repository=mock_repository,
            script_client=mock_client,
            renderer=MagicMock(),
        )

        result = await runner.start_job("job-003")

        # 검증: 시작되지 않음 (idempotent)
        assert result.started is False
        assert "already RUNNING" in result.message
        mock_client.get_render_spec.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_job_404_error(self, mock_repository):
        """backend 404 에러 시 job FAILED 처리."""
        job = RenderJobEntity(
            job_id="job-404",
            video_id="video-404",
            script_id="script-not-found",
            status="PENDING",
        )
        mock_repository.save(job)

        mock_client = AsyncMock()
        mock_client.get_render_spec = AsyncMock(
            side_effect=ScriptFetchError(
                script_id="script-not-found",
                status_code=404,
                message="Script not found",
                error_code="SCRIPT_NOT_FOUND",
            )
        )

        runner = RenderJobRunner(
            repository=mock_repository,
            script_client=mock_client,
            renderer=MagicMock(),
        )

        result = await runner.start_job("job-404")

        # 검증
        assert result.started is False
        assert result.error_code == "SCRIPT_NOT_FOUND"

        # Job이 FAILED 상태로 변경되었는지 확인
        failed_job = mock_repository.get("job-404")
        assert failed_job.status == "FAILED"
        assert failed_job.error_code == "SCRIPT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_start_job_401_error(self, mock_repository):
        """backend 401 에러 시 job FAILED 처리."""
        job = RenderJobEntity(
            job_id="job-401",
            video_id="video-401",
            script_id="script-001",
            status="PENDING",
        )
        mock_repository.save(job)

        mock_client = AsyncMock()
        mock_client.get_render_spec = AsyncMock(
            side_effect=ScriptFetchError(
                script_id="script-001",
                status_code=401,
                message="Unauthorized",
                error_code="SCRIPT_FETCH_UNAUTHORIZED",
            )
        )

        runner = RenderJobRunner(
            repository=mock_repository,
            script_client=mock_client,
            renderer=MagicMock(),
        )

        result = await runner.start_job("job-401")

        assert result.started is False
        assert result.error_code == "SCRIPT_FETCH_UNAUTHORIZED"

        failed_job = mock_repository.get("job-401")
        assert failed_job.status == "FAILED"

    @pytest.mark.asyncio
    async def test_start_job_500_error(self, mock_repository):
        """backend 500 에러 시 job FAILED 처리."""
        job = RenderJobEntity(
            job_id="job-500",
            video_id="video-500",
            script_id="script-001",
            status="PENDING",
        )
        mock_repository.save(job)

        mock_client = AsyncMock()
        mock_client.get_render_spec = AsyncMock(
            side_effect=ScriptFetchError(
                script_id="script-001",
                status_code=500,
                message="Server error",
                error_code="SCRIPT_FETCH_SERVER_ERROR",
            )
        )

        runner = RenderJobRunner(
            repository=mock_repository,
            script_client=mock_client,
            renderer=MagicMock(),
        )

        result = await runner.start_job("job-500")

        assert result.started is False
        assert result.error_code == "SCRIPT_FETCH_SERVER_ERROR"

        failed_job = mock_repository.get("job-500")
        assert failed_job.status == "FAILED"

    @pytest.mark.asyncio
    async def test_start_job_empty_render_spec(self, mock_repository):
        """빈 render-spec 시 job FAILED 처리."""
        job = RenderJobEntity(
            job_id="job-empty",
            video_id="video-empty",
            script_id="script-empty",
            status="PENDING",
        )
        mock_repository.save(job)

        mock_client = AsyncMock()
        mock_client.get_render_spec = AsyncMock(
            side_effect=EmptyRenderSpecError(script_id="script-empty")
        )

        runner = RenderJobRunner(
            repository=mock_repository,
            script_client=mock_client,
            renderer=MagicMock(),
        )

        result = await runner.start_job("job-empty")

        assert result.started is False
        assert result.error_code == "EMPTY_RENDER_SPEC"

        failed_job = mock_repository.get("job-empty")
        assert failed_job.status == "FAILED"

    @pytest.mark.asyncio
    async def test_start_job_not_found(self, mock_repository):
        """존재하지 않는 job_id."""
        mock_client = AsyncMock()

        runner = RenderJobRunner(
            repository=mock_repository,
            script_client=mock_client,
            renderer=MagicMock(),
        )

        result = await runner.start_job("non-existent-job")

        assert result.started is False
        assert result.error_code == "JOB_NOT_FOUND"
        assert result.job is None

    @pytest.mark.asyncio
    async def test_retry_job_without_render_spec(self, mock_repository):
        """render_spec_json이 없는 상태에서 retry 시도."""
        job = RenderJobEntity(
            job_id="job-no-spec",
            video_id="video-001",
            script_id="script-001",
            status="FAILED",
            render_spec_json=None,  # 스냅샷 없음
        )
        mock_repository.save(job)

        mock_client = AsyncMock()

        runner = RenderJobRunner(
            repository=mock_repository,
            script_client=mock_client,
            renderer=MagicMock(),
        )

        result = await runner.retry_job("job-no-spec")

        assert result.started is False
        assert result.error_code == "NO_RENDER_SPEC_FOR_RETRY"


# =============================================================================
# Repository Tests
# =============================================================================


class TestRenderJobRepository:
    """RenderJobRepository render_spec_json 관련 테스트."""

    def test_save_and_load_render_spec_json(self, mock_repository, sample_render_spec_response):
        """render_spec_json 저장 및 로드."""
        job = RenderJobEntity(
            job_id="job-repo-001",
            video_id="video-001",
            script_id="script-001",
            status="PENDING",
            render_spec_json=sample_render_spec_response,
        )
        mock_repository.save(job)

        loaded = mock_repository.get("job-repo-001")

        assert loaded is not None
        assert loaded.has_render_spec()
        assert loaded.render_spec_json["script_id"] == "script-001"
        assert len(loaded.render_spec_json["scenes"]) == 3

    def test_update_render_spec(self, mock_repository, sample_render_spec_response):
        """update_render_spec 메서드 테스트."""
        # 초기 job (render_spec 없음)
        job = RenderJobEntity(
            job_id="job-repo-002",
            video_id="video-001",
            script_id="script-001",
            status="PENDING",
        )
        mock_repository.save(job)

        # render_spec 업데이트
        success = mock_repository.update_render_spec(
            job_id="job-repo-002",
            render_spec_json=sample_render_spec_response,
        )

        assert success is True

        # 확인
        loaded = mock_repository.get("job-repo-002")
        assert loaded.has_render_spec()
        assert loaded.render_spec_json["script_id"] == "script-001"

    def test_has_render_spec_false_for_empty(self, mock_repository):
        """render_spec_json이 없으면 has_render_spec() == False."""
        job = RenderJobEntity(
            job_id="job-repo-003",
            video_id="video-001",
            script_id="script-001",
            status="PENDING",
            render_spec_json=None,
        )
        mock_repository.save(job)

        loaded = mock_repository.get("job-repo-003")
        assert loaded.has_render_spec() is False

    def test_has_render_spec_false_for_empty_dict(self, mock_repository):
        """render_spec_json이 빈 dict이면 has_render_spec() == False."""
        job = RenderJobEntity(
            job_id="job-repo-004",
            video_id="video-001",
            script_id="script-001",
            status="PENDING",
            render_spec_json={},
        )
        mock_repository.save(job)

        loaded = mock_repository.get("job-repo-004")
        assert loaded.has_render_spec() is False
