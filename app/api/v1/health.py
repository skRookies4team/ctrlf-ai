"""
헬스체크 API 모듈 (Health Check API Module)

쿠버네티스 및 로드밸런서의 헬스체크를 위한 엔드포인트를 제공합니다.
- /health: Liveness probe - 애플리케이션이 살아있는지 확인
- /health/ready: Readiness probe - 트래픽을 받을 준비가 되었는지 확인

Readiness 체크에서는 RAGFlow, LLM, Backend 서비스의 연결 상태를
확인하여 전체적인 서비스 준비 상태를 반환합니다.
"""

from typing import Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.clients.http_client import get_async_http_client
from app.clients.llm_client import LLMClient
from app.clients.ragflow_client import RagflowClient
from app.core.config import Settings, get_settings
from app.core.logging import get_logger

router = APIRouter(tags=["Health"])
logger = get_logger(__name__)


class HealthResponse(BaseModel):
    """
    헬스체크 응답 스키마

    Attributes:
        status: 서비스 상태 ("ok" 또는 "error")
        app: 애플리케이션 이름
        version: 애플리케이션 버전
        env: 실행 환경 (local/dev/prod)
    """

    status: str
    app: str
    version: str
    env: str


class ReadinessResponse(BaseModel):
    """
    Readiness 체크 응답 스키마

    Attributes:
        ready: 서비스가 트래픽을 받을 준비가 되었는지 여부
        checks: 각 의존성 서비스의 상태
            - ragflow: RAGFlow 서비스 상태 (설정된 경우에만)
            - llm: LLM 서비스 상태 (설정된 경우에만)
            - backend: Spring 백엔드 상태 (설정된 경우에만)
    """

    ready: bool
    checks: dict = {}


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness Check",
    description="애플리케이션이 정상적으로 실행 중인지 확인합니다.",
)
async def health_check(
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """
    Liveness 헬스체크 엔드포인트

    쿠버네티스의 livenessProbe 또는 로드밸런서의 헬스체크에 사용됩니다.
    애플리케이션이 살아있으면 200 OK를 반환합니다.

    Returns:
        HealthResponse: 애플리케이션 상태 정보
    """
    return HealthResponse(
        status="ok",
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        env=settings.APP_ENV,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness Check",
    description="서비스가 트래픽을 받을 준비가 되었는지 확인합니다.",
)
async def readiness_check(
    settings: Settings = Depends(get_settings),
) -> ReadinessResponse:
    """
    Readiness 헬스체크 엔드포인트

    쿠버네티스의 readinessProbe에 사용됩니다.
    모든 의존성 서비스에 연결 가능한 상태일 때만 ready=True를 반환합니다.

    의존성 체크 로직:
        - BASE_URL이 설정된 서비스만 체크합니다
        - 모든 설정된 서비스가 정상이면 ready=True
        - 하나라도 비정상이면 ready=False
        - 아무 서비스도 설정되지 않았으면 ready=True (기본 동작 유지)

    Returns:
        ReadinessResponse: 준비 상태 및 각 의존성 체크 결과
    """
    checks: Dict[str, bool] = {}

    # Phase 9: mock/real 모드에 따라 자동으로 URL 선택됨
    # RAGFlow 헬스체크
    if settings.ragflow_base_url:
        ragflow_client = RagflowClient()
        rag_ok = await ragflow_client.health_check()
        checks["ragflow"] = rag_ok

    # LLM 헬스체크
    if settings.llm_base_url:
        llm_client = LLMClient()
        llm_ok = await llm_client.health_check()
        checks["llm"] = llm_ok

    # Backend(Spring) 헬스체크
    if settings.backend_base_url:
        client = get_async_http_client()
        try:
            # TODO: 실제 백엔드 health 엔드포인트(/actuator/health 등)로 수정
            resp = await client.get(f"{settings.backend_base_url}/health")
            backend_ok = resp.status_code == 200
        except Exception as e:
            logger.exception("Backend health check error: %s", e)
            backend_ok = False
        checks["backend"] = backend_ok

    # ready 상태 결정
    # - checks가 비어 있으면 (의존성이 설정되지 않은 경우) ready=True
    # - 하나라도 False면 ready=False
    # - 모두 True면 ready=True
    if checks:
        ready = all(checks.values())
    else:
        ready = True

    return ReadinessResponse(ready=ready, checks=checks)
