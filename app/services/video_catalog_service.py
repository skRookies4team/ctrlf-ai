"""
Phase 22 수정: Video Catalog Service

영상 메타데이터의 서버 신뢰 소스입니다.
클라이언트가 보내는 total_duration 대신 서버에서 관리하는 값을 사용합니다.

MVP에서는 in-memory dict/stub로 구현합니다.
프로덕션에서는 DB 또는 외부 서비스로 교체 권장.
"""

from typing import Dict, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class VideoCatalogService:
    """영상 메타데이터 서버 신뢰 소스 (stub).

    영상의 총 길이(duration)를 서버에서 관리합니다.
    클라이언트가 보내는 값을 신뢰하지 않고 이 서비스의 값을 사용합니다.

    Usage:
        catalog = VideoCatalogService()
        catalog.register("VID-001", 600)  # 600초 영상 등록
        duration = catalog.get_duration("VID-001")  # 600 반환
    """

    def __init__(self) -> None:
        """VideoCatalogService 초기화."""
        self._catalog: Dict[str, int] = {}

    def register(self, video_id: str, duration_seconds: int) -> None:
        """영상을 카탈로그에 등록합니다.

        Args:
            video_id: 영상 ID
            duration_seconds: 영상 총 길이 (초)
        """
        self._catalog[video_id] = duration_seconds
        logger.debug(f"Video registered: {video_id} = {duration_seconds}s")

    def get_duration(self, video_id: str) -> Optional[int]:
        """서버 신뢰 duration을 반환합니다.

        Args:
            video_id: 영상 ID

        Returns:
            Optional[int]: 영상 총 길이 (초), 없으면 None
        """
        return self._catalog.get(video_id)

    def exists(self, video_id: str) -> bool:
        """영상이 카탈로그에 존재하는지 확인합니다."""
        return video_id in self._catalog

    def clear(self) -> None:
        """카탈로그 초기화 (테스트용)."""
        self._catalog.clear()


# =============================================================================
# 싱글턴 인스턴스
# =============================================================================

_video_catalog_service: Optional[VideoCatalogService] = None


def get_video_catalog_service() -> VideoCatalogService:
    """VideoCatalogService 싱글턴 인스턴스를 반환합니다."""
    global _video_catalog_service
    if _video_catalog_service is None:
        _video_catalog_service = VideoCatalogService()
    return _video_catalog_service


def clear_video_catalog_service() -> None:
    """VideoCatalogService 싱글턴 인스턴스를 제거합니다 (테스트용)."""
    global _video_catalog_service
    if _video_catalog_service is not None:
        _video_catalog_service.clear()
    _video_catalog_service = None
