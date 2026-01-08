"""
교육 영상 파이프라인 API

실제 동작하는 교육 영상 제작 파이프라인 엔드포인트.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.v1.source_sets import verify_internal_token
from app.core.logging import get_logger
from app.core.store.job_store import PipelineJob, SourceSetStatus, VideoJobStatus
from app.services.edu_pipeline_service import EducationPipelineService, get_edu_pipeline_service

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/ai", tags=["Education Pipeline"])


# =============================================================================
# Request/Response Models
# =============================================================================


class ScriptGenerationStartRequest(BaseModel):
    """스크립트 생성 시작 요청."""
    sourceSetId: str = Field(..., description="소스셋 ID")
    videoId: str = Field(..., description="영상 ID")
    educationId: str = Field(..., description="교육 ID")
    s3Urls: List[str] = Field(..., description="S3 URL 목록 (백엔드가 업로드한 문서들)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="메타데이터 (부서/카테고리/템플릿/언어 등)")
    requestId: Optional[str] = Field(None, description="요청 ID (멱등성)")
    traceId: Optional[str] = Field(None, description="추적 ID")


class ScriptGenerationStartResponse(BaseModel):
    """스크립트 생성 시작 응답."""
    sourceSetId: str
    videoId: str
    educationId: str
    status: str = Field(..., description="상태 (PROCESSING)")
    progress: int = Field(0, description="진행률 (0-100)")


class JobStatusResponse(BaseModel):
    """Job 상태 조회 응답."""
    sourceSetId: str
    videoId: str
    educationId: str
    sourceSetStatus: str
    scriptStatus: str
    videoJobStatus: Optional[str] = None
    progress: int
    failReason: Optional[str] = None
    scriptS3Key: Optional[str] = None
    videoS3Key: Optional[str] = None
    videoUrl: Optional[str] = None
    createdAt: str
    updatedAt: str


class RAGFlowCallbackRequest(BaseModel):
    """RAGFLOW 콜백 요청."""
    sourceSetId: str
    status: str = Field(..., description="상태 (COMPLETED, FAILED)")
    progress: int = Field(0, description="진행률 (0-100)")
    failReason: Optional[str] = None
    milvusCollection: Optional[str] = None
    milvusPartition: Optional[str] = None


class VideoGenerationStartRequest(BaseModel):
    """영상 생성 시작 요청."""
    videoId: str = Field(..., description="영상 ID")
    sourceSetId: str = Field(..., description="소스셋 ID")
    educationId: str = Field(..., description="교육 ID")
    scriptId: Optional[str] = Field(None, description="스크립트 ID (선택)")


class VideoGenerationStartResponse(BaseModel):
    """영상 생성 시작 응답."""
    videoId: str
    sourceSetId: str
    status: str = Field(..., description="상태 (PROCESSING)")
    progress: int = Field(0, description="진행률 (0-100)")


# =============================================================================
# Routes
# =============================================================================


@router.post(
    "/pipeline/script/start",
    response_model=ScriptGenerationStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="스크립트 생성 시작",
    description="""
스크립트 생성 파이프라인을 시작합니다.

**처리 흐름**:
1. 즉시 202 Accepted 반환 (비동기 처리)
2. RAGFLOW 전처리 시작
3. 전처리 완료 후 Milvus 검색 → 스크립트 2종 생성
4. 백엔드 콜백 또는 상태 조회 가능

**멱등성**:
- 동일 sourceSetId로 재요청 시 기존 job 반환
- 이미 완료된 경우 기존 결과 반환
""",
    dependencies=[Depends(verify_internal_token)],
)
async def start_script_generation(
    request: ScriptGenerationStartRequest,
):
    """스크립트 생성 파이프라인 시작."""
    service = get_edu_pipeline_service()
    
    try:
        job = await service.start_script_generation(
            source_set_id=request.sourceSetId,
            video_id=request.videoId,
            education_id=request.educationId,
            s3_urls=request.s3Urls,
            metadata=request.metadata,
            request_id=request.requestId,
            trace_id=request.traceId,
        )
        
        return ScriptGenerationStartResponse(
            sourceSetId=job.source_set_id,
            videoId=job.video_id,
            educationId=job.education_id,
            status=job.source_set_status.value,
            progress=job.progress,
        )
    
    except Exception as e:
        logger.error(f"Failed to start script generation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "SCRIPT_GENERATION_START_FAILED",
                "message": f"스크립트 생성 시작 실패: {str(e)[:200]}",
            },
        )


@router.get(
    "/pipeline/status",
    response_model=JobStatusResponse,
    summary="Job 상태 조회",
    description="""
Job 상태를 조회합니다.

**파라미터**:
- sourceSetId: 소스셋 ID로 조회
- videoId: 영상 ID로 조회 (sourceSetId 우선)

**상태**:
- sourceSetStatus: 전처리/스크립트 생성 상태
- scriptStatus: 스크립트 생성 상태
- videoJobStatus: 영상 생성 상태
""",
    dependencies=[Depends(verify_internal_token)],
)
async def get_job_status(
    sourceSetId: Optional[str] = None,
    videoId: Optional[str] = None,
):
    """Job 상태 조회."""
    if not sourceSetId and not videoId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "MISSING_PARAMETER", "message": "sourceSetId 또는 videoId가 필요합니다."},
        )
    
    service = get_edu_pipeline_service()
    
    try:
        job = await service.get_job_status(source_set_id=sourceSetId, video_id=videoId)
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": "JOB_NOT_FOUND", "message": "Job을 찾을 수 없습니다."},
            )
        
        return JobStatusResponse(
            sourceSetId=job.source_set_id,
            videoId=job.video_id,
            educationId=job.education_id,
            sourceSetStatus=job.source_set_status.value,
            scriptStatus=job.script_status.value,
            videoJobStatus=job.video_job_status.value if job.video_job_status else None,
            progress=job.progress,
            failReason=job.fail_reason,
            scriptS3Key=job.script_s3_key,
            videoS3Key=job.video_s3_key,
            videoUrl=job.video_url,
            createdAt=job.created_at.isoformat() + "Z",
            updatedAt=job.updated_at.isoformat() + "Z",
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get job status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_code": "STATUS_QUERY_FAILED", "message": f"상태 조회 실패: {str(e)[:200]}"},
        )


@router.post(
    "/pipeline/callbacks/ragflow",
    status_code=status.HTTP_200_OK,
    summary="RAGFLOW 콜백 수신",
    description="""
RAGFLOW 전처리 완료/실패 콜백을 수신합니다.

**처리 흐름**:
1. 콜백 수신
2. 상태 저장
3. 완료 시 스크립트 생성 자동 시작
""",
    dependencies=[Depends(verify_internal_token)],
)
async def handle_ragflow_callback(
    request: RAGFlowCallbackRequest,
):
    """RAGFLOW 콜백 수신."""
    service = get_edu_pipeline_service()
    
    try:
        await service.handle_ragflow_callback(
            source_set_id=request.sourceSetId,
            status=request.status,
            progress=request.progress,
            fail_reason=request.failReason,
            milvus_collection=request.milvusCollection,
            milvus_partition=request.milvusPartition,
        )
        
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"received": True, "sourceSetId": request.sourceSetId},
        )
    
    except Exception as e:
        logger.error(f"Failed to handle RAGFLOW callback: {e}", exc_info=True)
        # 콜백은 항상 200 반환 (재시도 방지)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"received": True, "error": str(e)[:200]},
        )


@router.post(
    "/pipeline/video/start",
    response_model=VideoGenerationStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="영상 생성 시작",
    description="""
영상 생성 파이프라인을 시작합니다.

**처리 흐름**:
1. 즉시 202 Accepted 반환 (비동기 처리)
2. Heygen job 생성
3. 폴링으로 완료 대기
4. 완료 시 다운로드 → S3 업로드
5. 백엔드 콜백

**멱등성**:
- 동일 videoId로 재요청 시 기존 job 반환
""",
    dependencies=[Depends(verify_internal_token)],
)
async def start_video_generation(
    request: VideoGenerationStartRequest,
):
    """영상 생성 파이프라인 시작."""
    service = get_edu_pipeline_service()
    
    try:
        job = await service.start_video_generation(
            video_id=request.videoId,
            source_set_id=request.sourceSetId,
            education_id=request.educationId,
            script_id=request.scriptId,
        )
        
        return VideoGenerationStartResponse(
            videoId=job.video_id,
            sourceSetId=job.source_set_id,
            status=job.video_job_status.value if job.video_job_status else "PENDING",
            progress=job.progress,
        )
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "INVALID_REQUEST", "message": str(e)},
        )
    except Exception as e:
        logger.error(f"Failed to start video generation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "VIDEO_GENERATION_START_FAILED",
                "message": f"영상 생성 시작 실패: {str(e)[:200]}",
            },
        )

