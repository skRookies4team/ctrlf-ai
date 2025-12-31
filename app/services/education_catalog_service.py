"""
Phase 22 수정 + Phase 26 확장: Education Catalog Service

교육 메타데이터의 서버 신뢰 소스입니다.
클라이언트가 보내는 is_mandatory_edu 대신 서버에서 판정합니다.

Phase 26 추가:
- 연간 재발행 지원 (year, due_date, expires_at)
- EXPIRED 동적 판정 (now > expires_at)
- 재발행 시 video_asset_id, script_text, subtitle_text 복사

4대교육(법정필수교육) 여부를 서버 측에서 판정하여
퀴즈 언락 게이트의 정확성을 보장합니다.

MVP에서는 ID prefix 규칙으로 구현합니다.
프로덕션에서는 DB 또는 외부 서비스로 교체 권장.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Dict, List, Optional, Set
from zoneinfo import ZoneInfo

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# 상수
# =============================================================================

# 4대교육 ID prefix 목록
MANDATORY_4TYPE_PREFIXES = (
    "EDU-4TYPE-",  # 4대교육 prefix
    "4EDU-",  # 대체 prefix
    "MANDATORY-",  # 필수교육 prefix
    "EDU-SEC-",  # 보안교육 prefix (Phase 26)
    "EDU-SAF-",  # 안전교육 prefix (Phase 26)
    "EDU-HAR-",  # 성희롱예방 prefix (Phase 26)
    "EDU-DIS-",  # 장애인인식 prefix (Phase 26)
)

# 서울 타임존
SEOUL_TZ = ZoneInfo("Asia/Seoul")


# =============================================================================
# 교육 상태 Enum
# =============================================================================

class EducationStatus:
    """교육 상태."""
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"


# =============================================================================
# 교육 메타데이터 모델
# =============================================================================

@dataclass
class EducationMeta:
    """교육 메타데이터.

    Phase 26: 연간 재발행 지원을 위한 확장된 메타데이터.
    """
    education_id: str
    year: int
    due_date: date  # 마감일 (이 날 23:59:59까지 유효)
    is_mandatory_4type: bool = False
    title: Optional[str] = None
    video_asset_id: Optional[str] = None  # 영상 자산 ID (재발행 시 복사)
    script_text: Optional[str] = None  # 스크립트 텍스트 (재발행 시 복사)
    subtitle_text: Optional[str] = None  # 자막 텍스트 (재발행 시 복사)
    video_ids: List[str] = field(default_factory=list)
    created_at: Optional[datetime] = None

    @property
    def expires_at(self) -> datetime:
        """만료 시각 계산: due_date 23:59:59 (Asia/Seoul)."""
        return datetime.combine(
            self.due_date,
            time(23, 59, 59),
            tzinfo=SEOUL_TZ
        )

    @property
    def status(self) -> str:
        """현재 상태 (동적 판정)."""
        now = datetime.now(SEOUL_TZ)
        if now > self.expires_at:
            return EducationStatus.EXPIRED
        return EducationStatus.ACTIVE

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """만료 여부 확인.

        Args:
            now: 현재 시각 (테스트용, 미지정 시 현재 시각 사용)

        Returns:
            bool: 만료 여부
        """
        if now is None:
            now = datetime.now(SEOUL_TZ)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=SEOUL_TZ)
        return now > self.expires_at

    def to_dict(self) -> dict:
        """딕셔너리로 변환."""
        return {
            "education_id": self.education_id,
            "year": self.year,
            "due_date": self.due_date.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
            "is_mandatory_4type": self.is_mandatory_4type,
            "title": self.title,
            "video_asset_id": self.video_asset_id,
            "script_text": self.script_text,
            "subtitle_text": self.subtitle_text,
            "video_ids": self.video_ids,
        }


# =============================================================================
# 교육 카탈로그 서비스
# =============================================================================

class EducationCatalogService:
    """교육 메타데이터 서버 신뢰 소스 (stub).

    교육이 4대교육(법정필수교육)인지 서버 측에서 판정합니다.
    클라이언트가 보내는 is_mandatory_edu 값을 신뢰하지 않습니다.

    Phase 26: 연간 재발행 및 EXPIRED 판정 지원.

    Usage:
        catalog = EducationCatalogService()

        # 4대교육 판정
        is_4type = catalog.is_mandatory_4type("EDU-4TYPE-001")  # True

        # EXPIRED 판정
        is_exp = catalog.is_expired("EDU-SEC-2024-001")  # True if past due

        # 재발행
        new_id = catalog.reissue("EDU-SEC-2024-001", 2025, date(2025, 12, 31))
    """

    def __init__(self) -> None:
        """EducationCatalogService 초기화."""
        # 명시적으로 등록된 4대교육 ID 목록 (prefix 외 추가 등록용)
        self._mandatory_4type_ids: Set[str] = set()
        # 교육 메타데이터 (education_id → EducationMeta)
        self._catalog: Dict[str, EducationMeta] = {}

    # =========================================================================
    # 4대교육 판정 (Phase 22 유지)
    # =========================================================================

    def is_mandatory_4type(self, education_id: str) -> bool:
        """4대교육(법정필수교육) 여부를 서버 측에서 판정합니다.

        판정 우선순위:
        1. 카탈로그에 등록된 메타데이터의 is_mandatory_4type
        2. 명시적으로 등록된 4대교육 ID 목록에 있으면 True
        3. ID가 4대교육 prefix로 시작하면 True
        4. 그 외 False

        Args:
            education_id: 교육 ID

        Returns:
            bool: 4대교육 여부
        """
        # 1. 카탈로그 메타데이터 확인
        meta = self._catalog.get(education_id)
        if meta is not None:
            return meta.is_mandatory_4type

        # 2. 명시적 등록 확인
        if education_id in self._mandatory_4type_ids:
            return True

        # 3. Prefix 규칙 확인
        for prefix in MANDATORY_4TYPE_PREFIXES:
            if education_id.startswith(prefix):
                return True

        return False

    def register_mandatory_4type(self, education_id: str) -> None:
        """교육을 4대교육으로 등록합니다.

        Args:
            education_id: 교육 ID
        """
        self._mandatory_4type_ids.add(education_id)
        logger.debug(f"Education registered as mandatory 4-type: {education_id}")

    def unregister_mandatory_4type(self, education_id: str) -> None:
        """교육을 4대교육에서 해제합니다.

        Args:
            education_id: 교육 ID
        """
        self._mandatory_4type_ids.discard(education_id)
        logger.debug(f"Education unregistered from mandatory 4-type: {education_id}")

    # =========================================================================
    # EXPIRED 판정 (Phase 26)
    # =========================================================================

    def is_expired(
        self,
        education_id: str,
        now: Optional[datetime] = None
    ) -> bool:
        """교육이 만료되었는지 확인합니다.

        카탈로그에 등록되지 않은 교육은 만료되지 않은 것으로 간주합니다.
        (하위 호환성 유지)

        Args:
            education_id: 교육 ID
            now: 현재 시각 (테스트용, 미지정 시 현재 시각 사용)

        Returns:
            bool: 만료 여부
        """
        meta = self._catalog.get(education_id)
        if meta is None:
            # 카탈로그에 없으면 만료되지 않은 것으로 간주
            return False
        return meta.is_expired(now)

    def get_status(self, education_id: str) -> Optional[str]:
        """교육 상태를 반환합니다.

        Args:
            education_id: 교육 ID

        Returns:
            Optional[str]: 상태 (ACTIVE/EXPIRED) 또는 None
        """
        meta = self._catalog.get(education_id)
        if meta is None:
            return None
        return meta.status

    # =========================================================================
    # 교육 등록/조회 (Phase 22 + Phase 26 확장)
    # =========================================================================

    def register_education(
        self,
        education_id: str,
        year: Optional[int] = None,
        due_date: Optional[date] = None,
        title: Optional[str] = None,
        is_mandatory_4type: bool = False,
        video_asset_id: Optional[str] = None,
        script_text: Optional[str] = None,
        subtitle_text: Optional[str] = None,
        video_ids: Optional[list] = None,
    ) -> EducationMeta:
        """교육을 카탈로그에 등록합니다.

        Args:
            education_id: 교육 ID
            year: 교육 연도 (미지정 시 현재 연도)
            due_date: 마감일 (미지정 시 해당 연도 12월 31일)
            title: 교육 제목
            is_mandatory_4type: 4대교육 여부
            video_asset_id: 영상 자산 ID
            script_text: 스크립트 텍스트
            subtitle_text: 자막 텍스트
            video_ids: 포함된 영상 ID 목록

        Returns:
            EducationMeta: 등록된 교육 메타데이터
        """
        # 기본값 설정
        if year is None:
            year = datetime.now(SEOUL_TZ).year
        if due_date is None:
            due_date = date(year, 12, 31)

        meta = EducationMeta(
            education_id=education_id,
            year=year,
            due_date=due_date,
            is_mandatory_4type=is_mandatory_4type,
            title=title,
            video_asset_id=video_asset_id,
            script_text=script_text,
            subtitle_text=subtitle_text,
            video_ids=video_ids or [],
            created_at=datetime.now(SEOUL_TZ),
        )

        self._catalog[education_id] = meta

        if is_mandatory_4type:
            self._mandatory_4type_ids.add(education_id)

        logger.debug(f"Education registered: {education_id}, year={year}, due_date={due_date}")
        return meta

    def get_education(self, education_id: str) -> Optional[EducationMeta]:
        """교육 메타데이터를 반환합니다.

        Args:
            education_id: 교육 ID

        Returns:
            Optional[EducationMeta]: 교육 메타데이터
        """
        return self._catalog.get(education_id)

    def exists(self, education_id: str) -> bool:
        """교육이 카탈로그에 존재하는지 확인합니다."""
        return education_id in self._catalog

    def list_active_educations(self) -> List[EducationMeta]:
        """활성 상태인 교육 목록을 반환합니다."""
        return [
            meta for meta in self._catalog.values()
            if meta.status == EducationStatus.ACTIVE
        ]

    # =========================================================================
    # 재발행 (Phase 26)
    # =========================================================================

    def reissue(
        self,
        source_education_id: str,
        target_year: int,
        new_due_date: date,
    ) -> EducationMeta:
        """교육을 재발행(복제 발행)합니다.

        작년 교육을 올해 교육으로 복제합니다.
        video_asset_id, script_text, subtitle_text는 그대로 복사됩니다.

        새 education_id 생성 규칙:
        - source id에서 연도만 치환: EDU-SEC-2025-001 → EDU-SEC-2026-001

        Args:
            source_education_id: 원본 교육 ID
            target_year: 대상 연도
            new_due_date: 새 마감일

        Returns:
            EducationMeta: 새로 생성된 교육 메타데이터

        Raises:
            ValueError: source가 없거나, target이 이미 존재하거나, due_date가 범위를 벗어남
        """
        # 1. source 존재 확인
        source = self._catalog.get(source_education_id)
        if source is None:
            raise ValueError(f"Source education not found: {source_education_id}")

        # 2. 새 education_id 생성 (연도 치환)
        new_education_id = self._generate_reissued_id(
            source_education_id,
            source.year,
            target_year
        )

        # 3. target 중복 확인
        if self.exists(new_education_id):
            raise ValueError(f"Target education already exists: {new_education_id}")

        # 4. due_date 범위 확인 (target_year 범위 내)
        if new_due_date.year != target_year:
            raise ValueError(
                f"Due date {new_due_date} is not in target year {target_year}"
            )

        # 5. 새 메타 생성 (자산/텍스트 복사)
        new_meta = EducationMeta(
            education_id=new_education_id,
            year=target_year,
            due_date=new_due_date,
            is_mandatory_4type=source.is_mandatory_4type,
            title=source.title,
            video_asset_id=source.video_asset_id,  # 복사
            script_text=source.script_text,  # 복사
            subtitle_text=source.subtitle_text,  # 복사
            video_ids=source.video_ids.copy(),
            created_at=datetime.now(SEOUL_TZ),
        )

        self._catalog[new_education_id] = new_meta

        if new_meta.is_mandatory_4type:
            self._mandatory_4type_ids.add(new_education_id)

        logger.info(
            f"Education reissued: {source_education_id} → {new_education_id}, "
            f"year={target_year}, due_date={new_due_date}"
        )

        return new_meta

    def _generate_reissued_id(
        self,
        source_id: str,
        source_year: int,
        target_year: int
    ) -> str:
        """재발행된 교육 ID를 생성합니다.

        규칙: source id에서 연도만 치환
        예: EDU-SEC-2025-001 → EDU-SEC-2026-001
        """
        # 연도 패턴 찾기 (4자리 숫자)
        pattern = str(source_year)
        if pattern in source_id:
            return source_id.replace(pattern, str(target_year), 1)

        # 연도가 없으면 suffix로 추가
        return f"{source_id}-{target_year}"

    # =========================================================================
    # 유틸리티
    # =========================================================================

    def clear(self) -> None:
        """카탈로그 초기화 (테스트용)."""
        self._mandatory_4type_ids.clear()
        self._catalog.clear()


# =============================================================================
# 싱글턴 인스턴스
# =============================================================================

_education_catalog_service: Optional[EducationCatalogService] = None


def get_education_catalog_service() -> EducationCatalogService:
    """EducationCatalogService 싱글턴 인스턴스를 반환합니다."""
    global _education_catalog_service
    if _education_catalog_service is None:
        _education_catalog_service = EducationCatalogService()
    return _education_catalog_service


def clear_education_catalog_service() -> None:
    """EducationCatalogService 싱글턴 인스턴스를 제거합니다 (테스트용)."""
    global _education_catalog_service
    if _education_catalog_service is not None:
        _education_catalog_service.clear()
    _education_catalog_service = None
