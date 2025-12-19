"""
Phase 37: Image Asset Service

Pillow를 사용하여 씬 이미지(1920x1080 PNG)를 생성합니다.

기능:
- VisualPlan에서 씬 이미지 생성
- highlight_terms 박스/밑줄 처리
- 그라데이션 배경
- 한글 폰트 지원

중간 산출물(씬 PNG)은 로컬 임시 폴더에만 저장하고 업로드하지 않습니다.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.visual_plan import VisualPlan

logger = get_logger(__name__)

# Pillow는 선택적 의존성 (없으면 mock 모드로 동작)
try:
    from PIL import Image, ImageDraw, ImageFont

    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    logger.warning("Pillow not installed. Image generation will use mock mode.")


@dataclass
class ImageConfig:
    """이미지 생성 설정."""

    width: int = 1920
    height: int = 1080
    background_color: Tuple[int, int, int] = (30, 30, 46)  # Dark blue-gray
    gradient_end_color: Tuple[int, int, int] = (45, 45, 68)  # Slightly lighter
    title_color: Tuple[int, int, int] = (255, 255, 255)  # White
    body_color: Tuple[int, int, int] = (200, 200, 210)  # Light gray
    highlight_color: Tuple[int, int, int] = (100, 180, 255)  # Light blue
    highlight_box_color: Tuple[int, int, int, int] = (100, 180, 255, 50)  # Semi-transparent
    title_font_size: int = 72
    body_font_size: int = 36
    padding: int = 100
    line_spacing: float = 1.5


class ImageAssetService:
    """씬 이미지 생성 서비스.

    Usage:
        service = ImageAssetService()
        image_path = service.generate_scene_image(
            visual_plan,
            output_dir="./temp",
            scene_index=0,
        )
    """

    def __init__(self, config: Optional[ImageConfig] = None):
        """서비스 초기화.

        Args:
            config: 이미지 생성 설정
        """
        settings = get_settings()
        self.config = config or ImageConfig(
            width=settings.VIDEO_WIDTH,
            height=settings.VIDEO_HEIGHT,
        )
        self._font_path = self._find_font_path()

    def _find_font_path(self) -> Optional[str]:
        """시스템에서 한글 폰트 경로 찾기."""
        # 우선순위: 환경변수 > 시스템 폰트
        env_font = os.getenv("VIDEO_FONT_PATH")
        if env_font and Path(env_font).exists():
            return env_font

        # 일반적인 한글 폰트 경로 (Windows/Linux/Mac)
        font_paths = [
            # Windows
            "C:/Windows/Fonts/malgun.ttf",  # 맑은 고딕
            "C:/Windows/Fonts/NanumGothic.ttf",
            # Linux
            "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            # Mac
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
            "/Library/Fonts/NanumGothic.ttf",
            # Docker (설치된 경우)
            "/usr/share/fonts/NanumGothic.ttf",
        ]

        for font_path in font_paths:
            if Path(font_path).exists():
                logger.info(f"Found font: {font_path}")
                return font_path

        logger.warning("No Korean font found. Text rendering may be limited.")
        return None

    @property
    def is_available(self) -> bool:
        """이미지 생성 가능 여부."""
        return PILLOW_AVAILABLE

    def generate_scene_image(
        self,
        plan: VisualPlan,
        output_dir: Path,
        scene_index: int,
    ) -> str:
        """씬 이미지 생성.

        Args:
            plan: 시각적 계획
            output_dir: 출력 디렉토리
            scene_index: 씬 인덱스 (파일명용)

        Returns:
            str: 생성된 이미지 파일 경로
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / f"scene_{scene_index:03d}.png"

        if not PILLOW_AVAILABLE:
            # Mock 모드: 빈 파일 생성
            output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            logger.warning(f"Mock image created (Pillow not available): {output_path}")
            return str(output_path)

        # 이미지 생성
        image = self._create_image(plan)
        image.save(str(output_path), "PNG", optimize=True)

        logger.debug(f"Scene image generated: {output_path}")
        return str(output_path)

    def generate_all_scene_images(
        self,
        plans: List[VisualPlan],
        output_dir: Path,
    ) -> List[str]:
        """여러 씬 이미지 생성.

        Args:
            plans: 시각적 계획 목록
            output_dir: 출력 디렉토리

        Returns:
            List[str]: 생성된 이미지 파일 경로 목록
        """
        paths = []
        for i, plan in enumerate(plans):
            path = self.generate_scene_image(plan, output_dir, i)
            paths.append(path)
        return paths

    def _create_image(self, plan: VisualPlan) -> "Image.Image":
        """Pillow로 이미지 생성."""
        config = self.config

        # 1. 그라데이션 배경 생성
        image = self._create_gradient_background()

        draw = ImageDraw.Draw(image)

        # 2. 폰트 로드
        title_font = self._get_font(config.title_font_size)
        body_font = self._get_font(config.body_font_size)

        # 3. 레이아웃 계산
        y_offset = config.padding + 100  # 상단 여백

        # 4. Title 그리기
        if plan.title:
            title_text = plan.title
            title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
            title_width = title_bbox[2] - title_bbox[0]

            # 중앙 정렬
            x = (config.width - title_width) // 2
            draw.text(
                (x, y_offset),
                title_text,
                font=title_font,
                fill=config.title_color,
            )

            y_offset += (title_bbox[3] - title_bbox[1]) + int(40 * config.line_spacing)

        # 5. Body 그리기 (highlight_terms 처리)
        if plan.body:
            self._draw_body_with_highlights(
                draw,
                plan.body,
                plan.highlight_terms,
                body_font,
                y_offset,
            )

        # 6. 하단 씬 번호 표시
        scene_label = f"Scene {plan.scene_id}"
        label_font = self._get_font(24)
        draw.text(
            (config.padding, config.height - config.padding),
            scene_label,
            font=label_font,
            fill=(128, 128, 140),
        )

        return image

    def _create_gradient_background(self) -> "Image.Image":
        """그라데이션 배경 생성."""
        config = self.config
        image = Image.new("RGB", (config.width, config.height))

        for y in range(config.height):
            # 수직 그라데이션
            ratio = y / config.height
            r = int(config.background_color[0] * (1 - ratio) + config.gradient_end_color[0] * ratio)
            g = int(config.background_color[1] * (1 - ratio) + config.gradient_end_color[1] * ratio)
            b = int(config.background_color[2] * (1 - ratio) + config.gradient_end_color[2] * ratio)

            for x in range(config.width):
                image.putpixel((x, y), (r, g, b))

        return image

    def _draw_body_with_highlights(
        self,
        draw: "ImageDraw.ImageDraw",
        body: str,
        highlight_terms: List[str],
        font: "ImageFont.FreeTypeFont",
        y_offset: int,
    ) -> None:
        """본문 텍스트 그리기 (하이라이트 처리).

        Args:
            draw: ImageDraw 객체
            body: 본문 텍스트
            highlight_terms: 강조할 키워드 목록
            font: 폰트
            y_offset: Y 시작 위치
        """
        config = self.config

        # 줄바꿈 처리
        max_width = config.width - (config.padding * 2)
        lines = self._wrap_text(body, font, max_width)

        current_y = y_offset

        for line in lines:
            # 중앙 정렬을 위한 X 계산
            line_bbox = draw.textbbox((0, 0), line, font=font)
            line_width = line_bbox[2] - line_bbox[0]
            x = (config.width - line_width) // 2

            # 하이라이트 처리
            self._draw_text_with_highlights(
                draw, line, highlight_terms, font, x, current_y
            )

            line_height = line_bbox[3] - line_bbox[1]
            current_y += int(line_height * config.line_spacing)

    def _draw_text_with_highlights(
        self,
        draw: "ImageDraw.ImageDraw",
        text: str,
        highlight_terms: List[str],
        font: "ImageFont.FreeTypeFont",
        x: int,
        y: int,
    ) -> None:
        """하이라이트가 적용된 텍스트 그리기."""
        config = self.config

        # 간단한 접근: 전체 텍스트를 먼저 그리고, 하이라이트 부분에 밑줄 추가
        draw.text((x, y), text, font=font, fill=config.body_color)

        # 각 하이라이트 키워드에 밑줄 추가
        current_x = x
        for term in highlight_terms:
            if term in text:
                # 키워드 위치 찾기
                idx = text.find(term)
                if idx >= 0:
                    # 키워드 앞부분까지의 너비
                    prefix = text[:idx]
                    prefix_bbox = draw.textbbox((0, 0), prefix, font=font)
                    prefix_width = prefix_bbox[2] - prefix_bbox[0]

                    # 키워드 너비
                    term_bbox = draw.textbbox((0, 0), term, font=font)
                    term_width = term_bbox[2] - term_bbox[0]
                    term_height = term_bbox[3] - term_bbox[1]

                    # 밑줄 그리기
                    underline_y = y + term_height + 5
                    draw.line(
                        [
                            (x + prefix_width, underline_y),
                            (x + prefix_width + term_width, underline_y),
                        ],
                        fill=config.highlight_color,
                        width=3,
                    )

    def _wrap_text(
        self,
        text: str,
        font: "ImageFont.FreeTypeFont",
        max_width: int,
    ) -> List[str]:
        """텍스트를 최대 너비에 맞게 줄바꿈."""
        if not PILLOW_AVAILABLE:
            return [text]

        words = text.split()
        lines = []
        current_line = []

        # 임시 이미지로 텍스트 크기 측정
        temp_image = Image.new("RGB", (1, 1))
        temp_draw = ImageDraw.Draw(temp_image)

        for word in words:
            test_line = " ".join(current_line + [word])
            bbox = temp_draw.textbbox((0, 0), test_line, font=font)
            width = bbox[2] - bbox[0]

            if width <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]

        if current_line:
            lines.append(" ".join(current_line))

        return lines if lines else [text]

    def _get_font(self, size: int) -> "ImageFont.FreeTypeFont":
        """폰트 로드."""
        if not PILLOW_AVAILABLE:
            return None

        if self._font_path:
            try:
                return ImageFont.truetype(self._font_path, size)
            except Exception as e:
                logger.warning(f"Failed to load font {self._font_path}: {e}")

        # 기본 폰트 사용
        try:
            return ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()


# =============================================================================
# Singleton Instance
# =============================================================================


_service: Optional[ImageAssetService] = None


def get_image_asset_service() -> ImageAssetService:
    """ImageAssetService 싱글톤 인스턴스 반환."""
    global _service
    if _service is None:
        _service = ImageAssetService()
    return _service


def clear_image_asset_service() -> None:
    """ImageAssetService 싱글톤 초기화 (테스트용)."""
    global _service
    _service = None
