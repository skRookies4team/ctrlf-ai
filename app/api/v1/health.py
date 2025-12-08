"""
헬스체크 API 모듈 (Health Check API Module)

쿠버네티스 및 로드밸런서의 헬스체크를 위한 엔드포인트를 제공합니다.
- /health: Liveness probe - 애플리케이션이 살아있는지 확인
- /health/ready: Readiness probe - 트래픽을 받을 준비가 되었는지 확인

나중에 ctrlf-ragflow, ctrlf-back 등 외부 서비스 연결 상태를
readiness 체크에 추가할 수 있습니다.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, get_settings

router = APIRouter(tags=["Health"])


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
        checks: 각 의존성 서비스의 상태 (나중에 확장 예정)
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

    Returns:
        ReadinessResponse: 준비 상태 및 각 의존성 체크 결과

    TODO: 나중에 아래 서비스들의 연결 상태를 점검하도록 확장
        - RAGFlow 서버 연결 상태 (settings.RAGFLOW_BASE_URL)
        - LLM 서버 연결 상태 (settings.LLM_BASE_URL)
        - ctrlf-back (Spring 백엔드) 연결 상태 (settings.BACKEND_BASE_URL)
        - 데이터베이스 연결 상태 (필요시)

    예시 확장 코드:
        checks = {}

        # RAGFlow 연결 체크
        if settings.RAGFLOW_BASE_URL:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{settings.RAGFLOW_BASE_URL}/health",
                        timeout=5.0
                    )
                    checks["ragflow"] = response.status_code == 200
            except Exception:
                checks["ragflow"] = False

        # 모든 체크가 통과해야 ready=True
        ready = all(checks.values()) if checks else True

        return ReadinessResponse(ready=ready, checks=checks)
    """
    # 현재는 단순히 ready=True 반환
    # 나중에 외부 서비스 연결 상태 점검 로직 추가 예정
    return ReadinessResponse(
        ready=True,
        checks={
            # TODO: 아래 항목들을 실제 체크 로직으로 교체
            # "ragflow": True,
            # "llm": True,
            # "backend": True,
        },
    )
