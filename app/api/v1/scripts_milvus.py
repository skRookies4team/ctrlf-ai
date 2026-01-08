"""
Milvus 기반 스크립트 생성 API (백엔드용)

도메인 기반으로 Milvus에서 문서를 검색하여 스크립트를 생성합니다.
test_video_script_from_milvus.py의 로직을 API로 구현.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.clients.backend_client import get_backend_client
from app.clients.milvus_client import get_milvus_client
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.video_script_generation_service import (
    ScriptGenerationOptions,
    VideoScriptGenerationService,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/ai", tags=["Script Generation (Milvus)"])


# =============================================================================
# Dependencies
# =============================================================================


async def verify_internal_token(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
) -> None:
    """내부 API 인증 토큰 검증."""
    settings = get_settings()
    expected_token = settings.BACKEND_INTERNAL_TOKEN

    if not expected_token:
        logger.warning("BACKEND_INTERNAL_TOKEN not configured, skipping auth")
        return

    if not x_internal_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "reason_code": "MISSING_TOKEN",
                "message": "X-Internal-Token 헤더가 필요합니다.",
            },
        )

    if x_internal_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "reason_code": "INVALID_TOKEN",
                "message": "유효하지 않은 인증 토큰입니다.",
            },
        )


# =============================================================================
# Request/Response Models
# =============================================================================


class MilvusScriptGenerateRequest(BaseModel):
    """Milvus 기반 스크립트 생성 요청."""

    video_id: str = Field(..., alias="videoId", description="영상 ID")
    domain: str = Field(..., description="도메인명 (예: 직장내괴롭힘교육)")
    language: str = Field(default="ko", description="언어")
    target_minutes: int = Field(default=4, description="목표 영상 길이(분)")
    max_chapters: int = Field(default=2, description="최대 챕터 수")
    max_scenes_per_chapter: int = Field(default=5, description="챕터당 최대 씬 수")
    style: str = Field(
        default="friendly_security_training", description="스크립트 스타일"
    )
    top_k: int = Field(default=50, description="Milvus 검색 결과 수")


class MilvusScriptGenerateResponse(BaseModel):
    """Milvus 기반 스크립트 생성 응답."""

    script_id: str = Field(..., alias="scriptId", description="생성된 스크립트 ID")
    video_id: str = Field(..., alias="videoId", description="영상 ID")
    domain: str = Field(..., description="도메인명")
    script: dict = Field(..., description="생성된 스크립트 JSON")
    source_text_length: int = Field(..., description="소스 텍스트 길이")

    class Config:
        populate_by_name = True


# =============================================================================
# API Endpoints
# =============================================================================


@router.post(
    "/scripts/generate-from-milvus",
    response_model=MilvusScriptGenerateResponse,
    status_code=status.HTTP_200_OK,
    summary="Milvus 기반 스크립트 생성 (Backend → AI)",
    description="""
도메인 기반으로 Milvus에서 문서를 검색하여 스크립트를 생성합니다.

**URL**: POST /internal/ai/scripts/generate-from-milvus

**호출 주체**: Spring 백엔드

**인증**: X-Internal-Token 헤더 필수

**처리 흐름**:
1. Milvus에서 도메인별 문서 검색
2. 검색된 문서 텍스트를 모아 source_text 구성
3. LLM으로 스크립트 생성
4. 백엔드 콜백 전송 (비동기)

**참고**: 
- domain은 Milvus의 dataset_id와 일치해야 합니다.
- 생성된 스크립트는 백엔드에 콜백으로 전달됩니다.
""",
    dependencies=[Depends(verify_internal_token)],
)
async def generate_script_from_milvus(
    request: MilvusScriptGenerateRequest,
):
    """Milvus 기반 스크립트 생성."""
    milvus = get_milvus_client()
    service = VideoScriptGenerationService()
    backend_client = get_backend_client()

    logger.info(
        f"Generating script from Milvus: video_id={request.video_id}, "
        f"domain={request.domain}"
    )

    try:
        # 1. Milvus에서 도메인별 문서 검색
        results = await milvus.search(
            query="교육 전체 내용 요약",
            domain=request.domain,
            top_k=request.top_k,
        )

        texts = [
            r["content"]
            for r in results
            if request.domain in r.get("metadata", {}).get("dataset_id", "")
        ]

        if not texts:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "reason_code": "NO_DOCUMENTS_FOUND",
                    "message": f"도메인 '{request.domain}'에 대한 문서를 찾을 수 없습니다.",
                },
            )

        source_text = "\n\n".join(texts)
        logger.info(f"Source text length: {len(source_text)}")

        # 2. 스크립트 생성 옵션 설정
        options = ScriptGenerationOptions(
            language=request.language,
            target_minutes=request.target_minutes,
            max_chapters=request.max_chapters,
            max_scenes_per_chapter=request.max_scenes_per_chapter,
            style=request.style,
        )

        # 3. 스크립트 생성
        video_script = await service.generate_script(
            video_id=request.video_id,
            source_text=source_text,
            options=options,
        )

        # 4. 스크립트 ID 생성 (임시 UUID, 실제로는 백엔드에서 관리)
        import uuid

        script_id = str(uuid.uuid4())

        # 5. 백엔드 콜백 전송 (비동기)
        import json

        script_json_str = json.dumps(video_script, ensure_ascii=False)
        asyncio.create_task(
            _notify_script_complete(
                backend_client=backend_client,
                video_id=request.video_id,
                script_id=script_id,
                script=script_json_str,
            )
        )

        return MilvusScriptGenerateResponse(
            script_id=script_id,
            video_id=request.video_id,
            domain=request.domain,
            script=video_script,
            source_text_length=len(source_text),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to generate script from Milvus: video_id={request.video_id}, "
            f"domain={request.domain}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason_code": "SCRIPT_GENERATION_FAILED",
                "message": f"스크립트 생성 실패: {str(e)[:200]}",
            },
        )


async def _notify_script_complete(
    backend_client,
    video_id: str,
    script_id: str,
    script: str,  # 이미 JSON 문자열
):
    """스크립트 생성 완료 콜백 전송."""
    try:
        await backend_client.notify_script_complete(
            material_id=video_id,  # video_id를 material_id로 사용
            script_id=script_id,
            script=script,
            version=1,
        )
        logger.info(f"Script complete callback sent: video_id={video_id}, script_id={script_id}")
    except Exception as e:
        logger.error(f"Failed to send script complete callback: {e}")

