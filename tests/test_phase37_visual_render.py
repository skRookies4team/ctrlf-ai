"""
Phase 37: Visual Render Tests

"영상처럼 보이게" 최소 연출 테스트:
- VisualPlan 추출 테스트
- ImageAssetService 이미지 생성 테스트
- VideoComposer animated 모드 테스트
- RealVideoRenderer animated 통합 테스트
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.video_composer import ComposerConfig, SceneInfo
from app.services.visual_plan import (
    VisualPlan,
    VisualPlanExtractor,
    clear_visual_plan_extractor,
    get_visual_plan_extractor,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_dir():
    """임시 디렉토리 픽스처."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_scene():
    """샘플 SceneInfo."""
    return SceneInfo(
        scene_id=1,
        narration='USB 메모리는 "플래시 드라이브"라고도 합니다. 용량은 보통 16GB에서 128GB까지 있습니다.',
        caption="USB 메모리 소개",
        on_screen_text=None,
        duration_sec=5.0,
    )


@pytest.fixture
def sample_scenes():
    """샘플 SceneInfo 목록."""
    return [
        SceneInfo(
            scene_id=1,
            narration="첫 번째 씬 나레이션입니다. API를 사용해서 데이터를 가져옵니다.",
            caption="API 소개",
            duration_sec=4.0,
        ),
        SceneInfo(
            scene_id=2,
            narration="두 번째 씬입니다. 처리 시간은 약 30초가 소요됩니다.",
            caption="처리 과정",
            duration_sec=5.0,
        ),
        SceneInfo(
            scene_id=3,
            narration='세 번째 씬입니다. "완료" 메시지가 표시되면 끝납니다.',
            on_screen_text="완료!",
            duration_sec=3.0,
        ),
    ]


# =============================================================================
# VisualPlan Extraction Tests
# =============================================================================


class TestVisualPlanExtractor:
    """VisualPlanExtractor 테스트."""

    def setup_method(self):
        """각 테스트 전 싱글톤 초기화."""
        clear_visual_plan_extractor()

    def test_extract_title_from_on_screen_text(self):
        """on_screen_text가 있으면 title로 사용."""
        scene = SceneInfo(
            scene_id=1,
            narration="긴 나레이션 텍스트",
            on_screen_text="화면 제목",
        )
        extractor = VisualPlanExtractor()
        plan = extractor.extract(scene)

        assert plan.title == "화면 제목"

    def test_extract_title_from_caption(self):
        """caption이 있고 on_screen_text가 없으면 caption 사용."""
        scene = SceneInfo(
            scene_id=1,
            narration="나레이션 텍스트입니다. 두 번째 문장입니다.",
            caption="캡션 제목",
        )
        extractor = VisualPlanExtractor()
        plan = extractor.extract(scene)

        assert plan.title == "캡션 제목"

    def test_extract_title_from_narration(self):
        """on_screen_text와 caption이 없으면 narration 첫 문장 사용."""
        scene = SceneInfo(
            scene_id=1,
            narration="첫 번째 문장입니다. 두 번째 문장이 있습니다.",
        )
        extractor = VisualPlanExtractor()
        plan = extractor.extract(scene)

        assert plan.title == "첫 번째 문장입니다"

    def test_extract_body_from_narration(self):
        """narration에서 본문 추출 (두 번째 문장부터)."""
        scene = SceneInfo(
            scene_id=1,
            narration="첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다.",
        )
        extractor = VisualPlanExtractor()
        plan = extractor.extract(scene)

        # 두 번째, 세 번째 문장이 body에 포함되어야 함
        assert "두 번째" in plan.body or "세 번째" in plan.body

    def test_extract_highlight_quoted_terms(self):
        """따옴표로 감싼 텍스트 추출."""
        scene = SceneInfo(
            scene_id=1,
            narration='이것은 "중요한 키워드"입니다. \'또 다른 키워드\'도 있습니다.',
        )
        extractor = VisualPlanExtractor()
        plan = extractor.extract(scene)

        assert "중요한 키워드" in plan.highlight_terms
        assert "또 다른 키워드" in plan.highlight_terms

    def test_extract_highlight_acronyms(self):
        """대문자 약어 추출 (API, USB 등)."""
        scene = SceneInfo(
            scene_id=1,
            narration="USB를 사용하여 API에 연결합니다. JSON 형식으로 전송됩니다.",
        )
        extractor = VisualPlanExtractor()
        plan = extractor.extract(scene)

        assert "USB" in plan.highlight_terms
        assert "API" in plan.highlight_terms
        assert "JSON" in plan.highlight_terms

    def test_extract_highlight_number_units(self):
        """숫자+단위 조합 추출."""
        scene = SceneInfo(
            scene_id=1,
            narration="용량은 16GB이고, 처리 시간은 30초입니다. 가격은 5000원입니다.",
        )
        extractor = VisualPlanExtractor()
        plan = extractor.extract(scene)

        assert "16GB" in plan.highlight_terms
        assert "30초" in plan.highlight_terms
        assert "5000원" in plan.highlight_terms

    def test_extract_highlight_emphasis_pattern(self):
        """강조 패턴 추출 (**강조**, [중요])."""
        scene = SceneInfo(
            scene_id=1,
            narration="이것은 **매우 중요**합니다. [핵심 개념]을 이해해야 합니다.",
        )
        extractor = VisualPlanExtractor()
        plan = extractor.extract(scene)

        assert "매우 중요" in plan.highlight_terms
        assert "핵심 개념" in plan.highlight_terms

    def test_extract_max_highlight_terms(self):
        """최대 highlight_terms 개수 제한."""
        scene = SceneInfo(
            scene_id=1,
            narration="USB, API, JSON, XML, HTTP, TCP, UDP, FTP가 있습니다.",
        )
        extractor = VisualPlanExtractor(max_highlight_terms=3)
        plan = extractor.extract(scene)

        assert len(plan.highlight_terms) <= 3

    def test_extract_all_scenes(self):
        """여러 씬에서 VisualPlan 목록 추출."""
        scenes = [
            SceneInfo(scene_id=1, narration="첫 번째 씬"),
            SceneInfo(scene_id=2, narration="두 번째 씬"),
            SceneInfo(scene_id=3, narration="세 번째 씬"),
        ]
        extractor = VisualPlanExtractor()
        plans = extractor.extract_all(scenes)

        assert len(plans) == 3
        assert plans[0].scene_id == 1
        assert plans[2].scene_id == 3

    def test_singleton_instance(self):
        """싱글톤 인스턴스 테스트."""
        extractor1 = get_visual_plan_extractor()
        extractor2 = get_visual_plan_extractor()

        assert extractor1 is extractor2


# =============================================================================
# ImageAssetService Tests
# =============================================================================


class TestImageAssetService:
    """ImageAssetService 테스트."""

    def test_service_available(self):
        """서비스 사용 가능 여부."""
        from app.services.image_asset_service import (
            ImageAssetService,
            PILLOW_AVAILABLE,
        )

        service = ImageAssetService()
        # Pillow 설치 여부에 따라 결과가 다름
        assert service.is_available == PILLOW_AVAILABLE

    def test_generate_scene_image_creates_file(self, temp_dir):
        """씬 이미지 생성 시 파일 생성됨."""
        from app.services.image_asset_service import ImageAssetService

        service = ImageAssetService()
        plan = VisualPlan(
            scene_id=1,
            title="테스트 제목",
            body="테스트 본문 내용입니다.",
            highlight_terms=["중요"],
            duration_sec=5.0,
        )

        image_path = service.generate_scene_image(
            plan=plan,
            output_dir=temp_dir,
            scene_index=0,
        )

        assert Path(image_path).exists()
        assert image_path.endswith(".png")

    def test_generate_scene_image_naming(self, temp_dir):
        """씬 이미지 파일명 규칙."""
        from app.services.image_asset_service import ImageAssetService

        service = ImageAssetService()
        plan = VisualPlan(scene_id=5, title="Test", body="Body")

        image_path = service.generate_scene_image(plan, temp_dir, scene_index=5)

        assert "scene_005.png" in image_path

    def test_generate_all_scene_images(self, temp_dir):
        """여러 씬 이미지 일괄 생성."""
        from app.services.image_asset_service import ImageAssetService

        service = ImageAssetService()
        plans = [
            VisualPlan(scene_id=1, title="Title 1", body="Body 1"),
            VisualPlan(scene_id=2, title="Title 2", body="Body 2"),
            VisualPlan(scene_id=3, title="Title 3", body="Body 3"),
        ]

        paths = service.generate_all_scene_images(plans, temp_dir)

        assert len(paths) == 3
        for path in paths:
            assert Path(path).exists()

    def test_singleton_instance(self):
        """싱글톤 인스턴스 테스트."""
        from app.services.image_asset_service import (
            clear_image_asset_service,
            get_image_asset_service,
        )

        clear_image_asset_service()
        service1 = get_image_asset_service()
        service2 = get_image_asset_service()

        assert service1 is service2


# =============================================================================
# VideoComposer Animated Mode Tests
# =============================================================================


class TestVideoComposerAnimatedMode:
    """VideoComposer animated 모드 테스트."""

    def test_config_visual_style_default(self):
        """기본 visual_style은 'basic'."""
        config = ComposerConfig()
        assert config.visual_style == "basic"

    def test_config_visual_style_animated(self):
        """visual_style을 'animated'로 설정 가능."""
        config = ComposerConfig(visual_style="animated")
        assert config.visual_style == "animated"

    def test_config_fade_duration(self):
        """fade_duration 설정."""
        config = ComposerConfig(fade_duration=1.0)
        assert config.fade_duration == 1.0

    def test_config_kenburns_zoom(self):
        """kenburns_zoom 설정."""
        config = ComposerConfig(kenburns_zoom=1.2)
        assert config.kenburns_zoom == 1.2

    def test_has_scene_images_true(self, temp_dir):
        """씬에 이미지가 있으면 True."""
        from app.services.video_composer import VideoComposer

        # 실제 파일 생성 (Path.exists() 체크를 위해)
        image1 = temp_dir / "image1.png"
        image2 = temp_dir / "image2.png"
        image1.write_bytes(b"fake png data")
        image2.write_bytes(b"fake png data")

        composer = VideoComposer(ComposerConfig(visual_style="animated"))
        scenes = [
            SceneInfo(scene_id=1, narration="Test", image_path=str(image1)),
            SceneInfo(scene_id=2, narration="Test", image_path=str(image2)),
        ]

        assert composer._has_scene_images(scenes) is True

    def test_has_scene_images_false_no_images(self):
        """씬에 이미지가 없으면 False."""
        from app.services.video_composer import VideoComposer

        composer = VideoComposer(ComposerConfig(visual_style="animated"))
        scenes = [
            SceneInfo(scene_id=1, narration="Test"),
            SceneInfo(scene_id=2, narration="Test"),
        ]

        assert composer._has_scene_images(scenes) is False

    def test_has_scene_images_false_partial(self):
        """일부 씬에만 이미지가 있으면 False."""
        from app.services.video_composer import VideoComposer

        composer = VideoComposer(ComposerConfig(visual_style="animated"))
        scenes = [
            SceneInfo(scene_id=1, narration="Test", image_path="/path/to/image1.png"),
            SceneInfo(scene_id=2, narration="Test"),  # No image
        ]

        assert composer._has_scene_images(scenes) is False


# =============================================================================
# RealVideoRenderer Animated Integration Tests
# =============================================================================


class TestRealVideoRendererAnimated:
    """RealVideoRenderer animated 모드 통합 테스트."""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings for animated mode."""
        settings = MagicMock()
        settings.VIDEO_VISUAL_STYLE = "animated"
        settings.VIDEO_WIDTH = 1920
        settings.VIDEO_HEIGHT = 1080
        settings.VIDEO_FPS = 30
        settings.VIDEO_FADE_DURATION = 0.5
        settings.VIDEO_KENBURNS_ZOOM = 1.1
        return settings

    @pytest.mark.asyncio
    async def test_render_slides_basic_mode_skips_image_gen(self, temp_dir):
        """basic 모드에서는 이미지 생성 건너뜀."""
        from app.services.video_renderer_real import (
            RealRenderJobContext,
            RealRendererConfig,
            RealVideoRenderer,
        )

        config = RealRendererConfig(
            output_dir=str(temp_dir),
            visual_style="basic",
        )
        renderer = RealVideoRenderer(config=config)

        ctx = RealRenderJobContext(
            job_id="test-job",
            video_id="test-video",
            script_id="test-script",
            script_json={},
            output_dir=temp_dir,
            scenes=[
                SceneInfo(scene_id=1, narration="Test 1"),
                SceneInfo(scene_id=2, narration="Test 2"),
            ],
        )

        await renderer._render_slides(ctx)

        # basic 모드에서는 scene.image_path가 None으로 유지됨
        for scene in ctx.scenes:
            assert scene.image_path is None

    @pytest.mark.asyncio
    async def test_render_slides_animated_mode_generates_images(self, temp_dir):
        """animated 모드에서 씬 이미지 생성."""
        from app.services.video_renderer_real import (
            RealRenderJobContext,
            RealRendererConfig,
            RealVideoRenderer,
        )

        config = RealRendererConfig(
            output_dir=str(temp_dir),
            visual_style="animated",
        )
        renderer = RealVideoRenderer(config=config)

        ctx = RealRenderJobContext(
            job_id="test-job",
            video_id="test-video",
            script_id="test-script",
            script_json={},
            output_dir=temp_dir,
            scenes=[
                SceneInfo(scene_id=1, narration="첫 번째 씬입니다. API를 사용합니다."),
                SceneInfo(scene_id=2, narration="두 번째 씬입니다. 30초가 걸립니다."),
            ],
        )

        await renderer._render_slides(ctx)

        # animated 모드에서는 scene.image_path가 설정됨
        for scene in ctx.scenes:
            assert scene.image_path is not None
            assert Path(scene.image_path).exists()

    @pytest.mark.asyncio
    async def test_render_slides_image_directory_created(self, temp_dir):
        """animated 모드에서 scene_images 디렉토리 생성."""
        from app.services.video_renderer_real import (
            RealRenderJobContext,
            RealRendererConfig,
            RealVideoRenderer,
        )

        config = RealRendererConfig(
            output_dir=str(temp_dir),
            visual_style="animated",
        )
        renderer = RealVideoRenderer(config=config)

        ctx = RealRenderJobContext(
            job_id="test-job",
            video_id="test-video",
            script_id="test-script",
            script_json={},
            output_dir=temp_dir,
            scenes=[SceneInfo(scene_id=1, narration="Test")],
        )

        await renderer._render_slides(ctx)

        image_dir = temp_dir / "scene_images"
        assert image_dir.exists()
        assert image_dir.is_dir()


# =============================================================================
# Config Settings Tests
# =============================================================================


class TestPhase37ConfigSettings:
    """Phase 37 설정 테스트."""

    def test_video_visual_style_default(self):
        """VIDEO_VISUAL_STYLE 기본값."""
        from app.core.config import Settings

        # 기본값 테스트 (환경변수 없이)
        settings = Settings()
        assert settings.VIDEO_VISUAL_STYLE in ["basic", "animated"]

    def test_video_width_default(self):
        """VIDEO_WIDTH 기본값."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.VIDEO_WIDTH == 1920

    def test_video_height_default(self):
        """VIDEO_HEIGHT 기본값."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.VIDEO_HEIGHT == 1080

    def test_video_fps_default(self):
        """VIDEO_FPS 기본값."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.VIDEO_FPS == 30

    def test_video_fade_duration_default(self):
        """VIDEO_FADE_DURATION 기본값."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.VIDEO_FADE_DURATION == 0.5

    def test_video_kenburns_zoom_default(self):
        """VIDEO_KENBURNS_ZOOM 기본값."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.VIDEO_KENBURNS_ZOOM == 1.1


# =============================================================================
# Integration Test: Full Pipeline
# =============================================================================


class TestAnimatedPipelineIntegration:
    """Animated 파이프라인 통합 테스트."""

    @pytest.mark.asyncio
    async def test_visual_plan_to_image_pipeline(self, temp_dir, sample_scene):
        """VisualPlan 추출 → 이미지 생성 파이프라인."""
        from app.services.image_asset_service import ImageAssetService
        from app.services.visual_plan import VisualPlanExtractor

        # 1. VisualPlan 추출
        extractor = VisualPlanExtractor()
        plan = extractor.extract(sample_scene)

        # 검증: 추출된 plan 확인
        assert plan.scene_id == 1
        assert "플래시 드라이브" in plan.highlight_terms  # 따옴표로 감싼 텍스트
        assert "USB" in plan.highlight_terms  # 약어
        assert "16GB" in plan.highlight_terms or "128GB" in plan.highlight_terms  # 숫자+단위

        # 2. 이미지 생성
        service = ImageAssetService()
        image_path = service.generate_scene_image(plan, temp_dir, 0)

        # 검증: 이미지 파일 생성됨
        assert Path(image_path).exists()
        assert Path(image_path).stat().st_size > 0

    @pytest.mark.asyncio
    async def test_multiple_scenes_pipeline(self, temp_dir, sample_scenes):
        """다중 씬 파이프라인 테스트."""
        from app.services.image_asset_service import ImageAssetService
        from app.services.visual_plan import VisualPlanExtractor

        extractor = VisualPlanExtractor()
        service = ImageAssetService()

        # 모든 씬에 대해 plan 추출 및 이미지 생성
        for i, scene in enumerate(sample_scenes):
            plan = extractor.extract(scene)
            image_path = service.generate_scene_image(plan, temp_dir, i)
            scene.image_path = image_path

        # 검증: 모든 씬에 이미지 경로가 설정됨
        for scene in sample_scenes:
            assert scene.image_path is not None
            assert Path(scene.image_path).exists()

        # 검증: 파일 개수 확인
        images = list(temp_dir.glob("*.png"))
        assert len(images) == len(sample_scenes)
