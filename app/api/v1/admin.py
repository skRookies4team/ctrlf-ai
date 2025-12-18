"""
Phase 26: Admin API Router Module

관리자 전용 API를 제공합니다.

Endpoints:
    - POST /api/admin/education/reissue: 교육 재발행 (복제 발행)
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.education_catalog_service import get_education_catalog_service

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# =============================================================================
# Request/Response Models
# =============================================================================


class EducationReissueRequest(BaseModel):
    """교육 재발행 요청."""
    source_education_id: str = Field(
        ...,
        description="원본 교육 ID",
        examples=["EDU-SEC-2025-001"],
    )
    target_year: int = Field(
        ...,
        description="대상 연도",
        examples=[2026],
        ge=2024,
        le=2100,
    )
    new_due_date: date = Field(
        ...,
        description="새 마감일",
        examples=["2026-12-31"],
    )


class EducationReissueResponse(BaseModel):
    """교육 재발행 응답."""
    success: bool = Field(..., description="성공 여부")
    new_education_id: str = Field(..., description="새로 생성된 교육 ID")
    source_education_id: str = Field(..., description="원본 교육 ID")
    target_year: int = Field(..., description="대상 연도")
    due_date: str = Field(..., description="마감일")
    expires_at: str = Field(..., description="만료 시각")
    copied_fields: dict = Field(
        ...,
        description="복사된 필드 정보",
    )


# =============================================================================
# POST /api/admin/education/reissue - 교육 재발행
# =============================================================================


@router.post(
    "/education/reissue",
    response_model=EducationReissueResponse,
    summary="Reissue Education",
    description=(
        "교육을 재발행(복제 발행)합니다. "
        "작년 교육을 올해 교육으로 복제하며, video_asset_id/script/subtitle은 그대로 복사됩니다."
    ),
    responses={
        200: {"description": "Successfully reissued education"},
        400: {"description": "Due date not in target year range"},
        404: {"description": "Source education not found"},
        409: {"description": "Target education already exists"},
    },
)
async def reissue_education(
    request: EducationReissueRequest,
) -> EducationReissueResponse:
    """
    교육을 재발행(복제 발행)합니다.

    Phase 26: 연간 재발행 지원

    **새 education_id 생성 규칙:**
    - source id에서 연도만 치환: EDU-SEC-2025-001 → EDU-SEC-2026-001

    **복사되는 필드:**
    - video_asset_id
    - script_text
    - subtitle_text
    - is_mandatory_4type

    **Request Body:**
    - `source_education_id`: 원본 교육 ID
    - `target_year`: 대상 연도
    - `new_due_date`: 새 마감일

    **Response:**
    - `success`: 성공 여부
    - `new_education_id`: 새로 생성된 교육 ID
    - `copied_fields`: 복사된 필드 정보

    Args:
        request: 재발행 요청

    Returns:
        EducationReissueResponse: 재발행 결과

    Raises:
        HTTPException: 404 (source 없음), 409 (target 중복), 400 (due_date 범위 오류)
    """
    catalog = get_education_catalog_service()

    try:
        new_meta = catalog.reissue(
            source_education_id=request.source_education_id,
            target_year=request.target_year,
            new_due_date=request.new_due_date,
        )
    except ValueError as e:
        error_message = str(e)

        # Error type mapping
        if "not found" in error_message.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "reason_code": "SOURCE_NOT_FOUND",
                    "message": error_message,
                },
            )
        elif "already exists" in error_message.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "reason_code": "TARGET_EXISTS",
                    "message": error_message,
                },
            )
        elif "not in target year" in error_message.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "reason_code": "DUE_DATE_OUT_OF_RANGE",
                    "message": error_message,
                },
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "reason_code": "REISSUE_ERROR",
                    "message": error_message,
                },
            )

    return EducationReissueResponse(
        success=True,
        new_education_id=new_meta.education_id,
        source_education_id=request.source_education_id,
        target_year=new_meta.year,
        due_date=new_meta.due_date.isoformat(),
        expires_at=new_meta.expires_at.isoformat(),
        copied_fields={
            "video_asset_id": new_meta.video_asset_id,
            "script_text": new_meta.script_text is not None,
            "subtitle_text": new_meta.subtitle_text is not None,
            "is_mandatory_4type": new_meta.is_mandatory_4type,
        },
    )


# =============================================================================
# GET /api/admin/education/{education_id} - 교육 메타데이터 조회 (개발/디버깅용)
# =============================================================================


class EducationMetaResponse(BaseModel):
    """교육 메타데이터 응답."""
    education_id: str
    year: int
    due_date: str
    expires_at: str
    status: str
    is_mandatory_4type: bool
    title: Optional[str] = None
    video_asset_id: Optional[str] = None
    has_script: bool
    has_subtitle: bool


@router.get(
    "/education/{education_id}",
    response_model=EducationMetaResponse,
    summary="Get Education Meta",
    description="교육 메타데이터를 조회합니다 (개발/디버깅용).",
    responses={
        200: {"description": "Education metadata"},
        404: {"description": "Education not found"},
    },
)
async def get_education_meta(education_id: str) -> EducationMetaResponse:
    """
    교육 메타데이터를 조회합니다.

    **Response:**
    - `education_id`: 교육 ID
    - `year`: 교육 연도
    - `due_date`: 마감일
    - `expires_at`: 만료 시각
    - `status`: 상태 (ACTIVE/EXPIRED)

    Args:
        education_id: 교육 ID

    Returns:
        EducationMetaResponse: 교육 메타데이터

    Raises:
        HTTPException: 교육이 없으면 404
    """
    catalog = get_education_catalog_service()
    meta = catalog.get_education(education_id)

    if meta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "EDUCATION_NOT_FOUND",
                "message": f"Education {education_id} not found in catalog",
            },
        )

    return EducationMetaResponse(
        education_id=meta.education_id,
        year=meta.year,
        due_date=meta.due_date.isoformat(),
        expires_at=meta.expires_at.isoformat(),
        status=meta.status,
        is_mandatory_4type=meta.is_mandatory_4type,
        title=meta.title,
        video_asset_id=meta.video_asset_id,
        has_script=meta.script_text is not None,
        has_subtitle=meta.subtitle_text is not None,
    )
