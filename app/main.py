"""
FastAPI 애플리케이션 메인 모듈

ctrlf-ai-gateway 서비스의 진입점입니다.
FastAPI 인스턴스를 생성하고, 라우터를 등록하며, 미들웨어를 설정합니다.

연동 예정 서비스:
    - ctrlf-back (Spring 백엔드): 사용자 인증, 비즈니스 로직
    - ctrlf-ragflow: RAG 처리
    - ctrlf-front (React): 프론트엔드 UI
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1 import (
    chat,
    chat_stream,
    faq,
    gap_suggestions,
    health,
    internal_rag,
    quiz_generate,
    render_jobs,
    scripts,
    ws_render,
)
from app.clients.http_client import close_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

# 설정 및 로거 초기화
settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    애플리케이션 라이프사이클 관리

    FastAPI의 lifespan 이벤트를 사용하여 시작/종료 시 필요한 작업을 수행합니다.

    시작 시:
        - 로깅 설정
        - (향후) 데이터베이스 연결
        - (향후) 외부 서비스 클라이언트 초기화

    종료 시:
        - (향후) 리소스 정리
        - (향후) 연결 종료
    """
    # 시작 시 실행
    setup_logging(settings)
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {settings.APP_ENV}")
    logger.info(f"AI_ENV: {settings.AI_ENV}")
    logger.info(f"LLM_BASE_URL: {settings.llm_base_url}")
    logger.info(f"LLM_MODEL_NAME: {settings.LLM_MODEL_NAME}")
    logger.info(f"RAGFLOW_BASE_URL: {settings.ragflow_base_url}")

    # TODO: 향후 추가될 초기화 작업
    # - 데이터베이스 연결 풀 초기화
    # - 캐시 연결 (Redis 등)
    # 참고: httpx.AsyncClient는 lazy-init 방식으로 첫 호출 시 생성됨

    try:
        yield  # 애플리케이션 실행
    finally:
        # 종료 시 실행
        await close_async_http_client()
        logger.info(f"Shutting down {settings.APP_NAME}")

        # TODO: 향후 추가될 정리 작업
        # - 데이터베이스 연결 종료
        # - 캐시 연결 종료


# FastAPI 인스턴스 생성
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "CTRL+F AI Gateway 서비스\n\n"
        "RAGFlow 및 LLM 서비스와 연동하여 AI 기능을 제공하는 게이트웨이입니다.\n\n"
        "연동 서비스:\n"
        "- ctrlf-back (Spring 백엔드)\n"
        "- ctrlf-ragflow (RAG 처리)\n"
        "- ctrlf-front (React 프론트엔드)"
    ),
    lifespan=lifespan,
    # OpenAPI 문서 경로 설정
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS 미들웨어 설정
# TODO: 프로덕션 환경에서는 settings.CORS_ORIGINS를 파싱하여 특정 도메인만 허용
# 예: ctrlf-front의 도메인만 허용
#
# 현재 설정 (개발 환경용):
# - allow_origins=["*"]: 모든 origin 허용
# - allow_credentials=True: 쿠키/인증 헤더 허용
# - allow_methods=["*"]: 모든 HTTP 메서드 허용
# - allow_headers=["*"]: 모든 헤더 허용
#
# 프로덕션 설정 예시:
# origins = settings.CORS_ORIGINS.split(",") if settings.CORS_ORIGINS != "*" else ["*"]
origins = (
    settings.CORS_ORIGINS.split(",")
    if settings.CORS_ORIGINS != "*"
    else ["*"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
#
# prefix 선택에 대한 고려사항:
#
# 옵션 1: prefix="/" (현재 선택)
#   - 헬스체크 경로: /health, /health/ready
#   - 장점: 쿠버네티스 헬스체크 설정이 간단함
#   - 장점: 로드밸런서/프록시 설정이 간단함
#   - 단점: API 버전 관리가 라우터 레벨에서 필요
#
# 옵션 2: prefix="/api/v1"
#   - 헬스체크 경로: /api/v1/health, /api/v1/health/ready
#   - 장점: API 버전이 URL에 명시적으로 표현됨
#   - 장점: 향후 /api/v2 추가가 용이
#   - 단점: 헬스체크 경로가 길어짐
#
# 현재는 헬스체크의 접근성을 우선시하여 "/" prefix를 사용합니다.
# AI API들은 /ai prefix로 라우터 내부에서 정의되어 있습니다.
# 향후 /api/v1 prefix로 마이그레이션할 경우 아래 주석 참고:
#   app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
#   app.include_router(rag.router, prefix="/api/v1", tags=["RAG"])
app.include_router(health.router, prefix="", tags=["Health"])

# AI API routers
# - POST /ai/chat/messages: AI chat response generation
# - POST /ai/gap/policy-edu/suggestions: RAG Gap 보완 제안
# - POST /ai/quiz/generate: 퀴즈 자동 생성
# - POST /ai/faq/generate: FAQ 초안 생성
app.include_router(chat.router, tags=["Chat"])
app.include_router(gap_suggestions.router, prefix="/ai", tags=["Gap Suggestions"])
app.include_router(quiz_generate.router, prefix="/ai", tags=["Quiz Generate"])
app.include_router(faq.router, prefix="/ai", tags=["FAQ"])

# Chat Stream API (HTTP 청크 스트리밍)
# - POST /ai/chat/stream: 스트리밍 채팅 응답 생성 (NDJSON)
# 백엔드(Spring)가 NDJSON을 줄 단위로 읽어서 SSE로 변환
app.include_router(chat_stream.router, tags=["Chat Stream"])

# Internal RAG API (Direct Milvus Integration)
# - POST /internal/rag/index: 문서 인덱싱 요청
# - POST /internal/rag/delete: 문서 삭제 요청
# - GET /internal/jobs/{job_id}: 작업 상태 조회
app.include_router(internal_rag.router, tags=["Internal RAG"])

# Scripts API (스크립트 CRUD + 편집)
# - POST /api/scripts: 스크립트 생성
# - GET /api/scripts/{script_id}: 스크립트 조회
# - POST /api/videos/{video_id}/scripts/generate: 스크립트 자동 생성
# - GET /api/scripts/{script_id}/editor: 편집용 뷰 조회
# - PATCH /api/scripts/{script_id}/editor: 씬 부분 수정
app.include_router(scripts.router, tags=["Scripts"])

# Render Jobs API (렌더 잡 CRUD)
# - POST /api/videos/{video_id}/render-jobs: 렌더 잡 생성 (idempotent)
# - GET /api/videos/{video_id}/render-jobs: 잡 목록 조회
# - GET /api/videos/{video_id}/render-jobs/{job_id}: 잡 상세 조회
# - POST /api/videos/{video_id}/render-jobs/{job_id}/cancel: 잡 취소
# - GET /api/videos/{video_id}/assets/published: 발행된 에셋 조회
app.include_router(render_jobs.router, tags=["Render Jobs"])

# Backend → AI 호출 API (영상 생성 시작/재시도)
# - POST /ai/video/job/{job_id}/start: 영상 생성 시작
# - POST /ai/video/job/{job_id}/retry: 영상 생성 재시도
app.include_router(render_jobs.ai_router, prefix="/ai", tags=["Video Job (Backend → AI)"])

# WebSocket Render Progress (실시간 렌더 진행률)
# - WS /ws/videos/{video_id}/render-progress: 렌더 진행률 실시간 구독
app.include_router(ws_render.router, prefix="/ws", tags=["WebSocket"])
