"""
Phase 38: Render Spec Models

백엔드에서 조회한 render-spec 데이터 모델.
Job 시작 시 스냅샷으로 저장되어 TTS/렌더링에 사용됩니다.

Usage:
    from app.models.render_spec import RenderSpec, RenderScene

    spec = RenderSpec(
        script_id="uuid",
        video_id="uuid",
        title="교육 제목",
        total_duration_sec=120,
        scenes=[...]
    )
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Pydantic Models (API 응답 파싱용)
# =============================================================================


class VisualSpec(BaseModel):
    """시각적 표현 사양.

    Attributes:
        type: 시각 타입 (TEXT_HIGHLIGHT, IMAGE, etc.)
        text: 표시할 텍스트
        highlight_terms: 강조할 용어 목록
    """
    type: str = Field(default="TEXT_HIGHLIGHT", description="시각 타입")
    text: Optional[str] = Field(default=None, description="표시 텍스트")
    highlight_terms: List[str] = Field(default_factory=list, description="강조 용어")


class RenderScene(BaseModel):
    """렌더링할 씬 정보.

    Attributes:
        scene_id: 씬 고유 ID
        scene_order: 씬 순서 (1부터 시작)
        chapter_title: 챕터 제목
        purpose: 씬 목적 (hook, explanation, example, summary 등)
        narration: 나레이션 텍스트 (TTS 입력)
        caption: 화면 캡션 텍스트
        duration_sec: 씬 지속 시간 (초)
        visual_spec: 시각적 표현 사양
    """
    scene_id: str = Field(..., description="씬 ID")
    scene_order: int = Field(..., ge=1, description="씬 순서")
    chapter_title: str = Field(default="", description="챕터 제목")
    purpose: str = Field(default="", description="씬 목적")
    narration: str = Field(default="", description="나레이션 텍스트")
    caption: str = Field(default="", description="화면 캡션")
    duration_sec: float = Field(default=5.0, ge=0, description="씬 지속 시간")
    visual_spec: Optional[VisualSpec] = Field(default=None, description="시각 사양")


class RenderSpec(BaseModel):
    """렌더 스펙 (백엔드에서 조회).

    Job 시작 시 백엔드에서 조회하여 스냅샷으로 저장합니다.
    이후 모든 렌더링 작업은 이 스냅샷을 기반으로 수행됩니다.

    Attributes:
        script_id: 스크립트 ID
        video_id: 비디오 ID
        title: 영상 제목
        total_duration_sec: 총 영상 길이 (초)
        scenes: 씬 목록
    """
    script_id: str = Field(..., description="스크립트 ID")
    video_id: str = Field(..., description="비디오 ID")
    title: str = Field(default="", description="영상 제목")
    total_duration_sec: float = Field(default=0, ge=0, description="총 길이")
    scenes: List[RenderScene] = Field(default_factory=list, description="씬 목록")

    def is_empty(self) -> bool:
        """씬이 비어있는지 확인."""
        return len(self.scenes) == 0

    def get_scene_count(self) -> int:
        """씬 개수 반환."""
        return len(self.scenes)

    def to_raw_json(self) -> Dict[str, Any]:
        """raw_json 형태로 변환 (기존 코드 호환용).

        Returns:
            dict: chapters/scenes 형태의 JSON
        """
        # 씬을 챕터별로 그룹화
        chapters_dict: Dict[str, List[Dict]] = {}
        for scene in self.scenes:
            chapter = scene.chapter_title or "기본 챕터"
            if chapter not in chapters_dict:
                chapters_dict[chapter] = []
            chapters_dict[chapter].append({
                "scene_id": scene.scene_id,
                "scene_order": scene.scene_order,
                "purpose": scene.purpose,
                "narration": scene.narration,
                "caption": scene.caption,
                "duration_sec": scene.duration_sec,
                "visual_spec": scene.visual_spec.model_dump() if scene.visual_spec else None,
            })

        chapters = []
        for i, (chapter_title, scenes) in enumerate(chapters_dict.items(), 1):
            chapters.append({
                "chapter_order": i,
                "chapter_title": chapter_title,
                "scenes": scenes,
            })

        return {
            "title": self.title,
            "total_duration_sec": self.total_duration_sec,
            "chapters": chapters,
        }


# =============================================================================
# API Response Models
# =============================================================================


class RenderSpecResponse(BaseModel):
    """GET /internal/scripts/{scriptId}/render-spec 응답.

    백엔드에서 반환하는 render-spec 형식입니다.
    """
    script_id: str = Field(..., description="스크립트 ID")
    video_id: str = Field(..., description="비디오 ID")
    title: str = Field(default="", description="영상 제목")
    total_duration_sec: float = Field(default=0, description="총 길이")
    scenes: List[RenderScene] = Field(default_factory=list, description="씬 목록")

    def to_render_spec(self) -> RenderSpec:
        """RenderSpec으로 변환."""
        return RenderSpec(
            script_id=self.script_id,
            video_id=self.video_id,
            title=self.title,
            total_duration_sec=self.total_duration_sec,
            scenes=self.scenes,
        )


# =============================================================================
# Validation Utilities
# =============================================================================


def validate_and_normalize_scene(
    scene: RenderScene,
    default_duration: float = 5.0
) -> tuple[RenderScene, List[str]]:
    """씬 데이터 검증 및 정규화.

    Args:
        scene: 검증할 씬
        default_duration: duration_sec <= 0일 때 사용할 기본값

    Returns:
        tuple: (정규화된 씬, 경고 메시지 목록)
    """
    warnings: List[str] = []

    # duration_sec <= 0이면 기본값으로 보정
    if scene.duration_sec <= 0:
        warnings.append(
            f"Scene {scene.scene_id}: duration_sec={scene.duration_sec} -> {default_duration}"
        )
        scene = scene.model_copy(update={"duration_sec": default_duration})

    # narration이 빈 문자열이면 경고
    if not scene.narration or not scene.narration.strip():
        warnings.append(
            f"Scene {scene.scene_id}: empty narration (TTS will be skipped)"
        )

    return scene, warnings


def validate_render_spec(
    spec: RenderSpec,
    default_duration: float = 5.0
) -> tuple[RenderSpec, List[str]]:
    """RenderSpec 전체 검증 및 정규화.

    Args:
        spec: 검증할 RenderSpec
        default_duration: 기본 duration 값

    Returns:
        tuple: (정규화된 RenderSpec, 경고 메시지 목록)
    """
    all_warnings: List[str] = []
    normalized_scenes: List[RenderScene] = []

    for scene in spec.scenes:
        normalized, warnings = validate_and_normalize_scene(scene, default_duration)
        normalized_scenes.append(normalized)
        all_warnings.extend(warnings)

    # total_duration_sec 재계산
    total = sum(s.duration_sec for s in normalized_scenes)

    normalized_spec = RenderSpec(
        script_id=spec.script_id,
        video_id=spec.video_id,
        title=spec.title,
        total_duration_sec=total,
        scenes=normalized_scenes,
    )

    return normalized_spec, all_warnings
