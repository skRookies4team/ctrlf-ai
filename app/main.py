"""
FastAPI 애플리케이션 메인 파일
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# .env 파일 로드 (최우선!)
load_dotenv()

from app.routers import ingest, reports, search, rag

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("ingestion_service.log")
    ]
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """애플리케이션 시작/종료 시 실행"""
    # 시작 시
    logger.info("Starting Ingestion Service...")
    logger.info("Application is ready to accept requests")
    yield
    # 종료 시
    logger.info("Shutting down Ingestion Service...")


# FastAPI 앱 생성
app = FastAPI(
    title="Document Ingestion Service",
    description="PDF 문서 전처리·청킹·임베딩 Ingestion Service",
    version="1.0.0",
    lifespan=lifespan
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 프로덕션에서는 특정 도메인으로 제한
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(ingest.router)
app.include_router(reports.router)
app.include_router(search.router)
app.include_router(rag.router)


@app.get("/")
async def root():
    """루트 엔드포인트"""
    return {
        "service": "Document Ingestion Service",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "health": "/api/v1/ingest/health",
            "ingest_file": "POST /api/v1/ingest/file",
            "get_reports": "GET /api/v1/ingest/reports",
            "get_report": "GET /api/v1/ingest/reports/{ingest_id}",
            "search": "POST /api/v1/search",
            "vector_store_stats": "GET /api/v1/vector-store/stats",
            "rag_query": "POST /api/v1/rag/query",
            "rag_health": "GET /api/v1/rag/health",
            "docs": "/docs",
            "redoc": "/redoc"
        }
    }


@app.get("/health")
async def health():
    """전역 헬스체크"""
    return {
        "status": "healthy",
        "service": "Document Ingestion Service",
        "version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
