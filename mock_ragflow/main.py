"""
Mock RAGFlow Server for Integration Testing

Phase 8: Docker Compose 환경에서 RAG 검색을 시뮬레이션하는 Mock 서버입니다.

엔드포인트:
- POST /search: RAG 문서 검색
- GET /health: 헬스체크
- GET /stats: 호출 통계 (테스트 검증용)
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

# 로깅 설정
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Mock RAGFlow Server",
    description="Integration test용 RAGFlow 시뮬레이션 서버",
    version="0.1.0",
)


# =============================================================================
# 모델 정의
# =============================================================================


class SearchRequest(BaseModel):
    """RAG 검색 요청."""

    query: str
    top_k: int = 5
    dataset: Optional[str] = None
    user_role: Optional[str] = None
    department: Optional[str] = None


class RagDocument(BaseModel):
    """RAG 검색 결과 문서."""

    doc_id: str
    title: str
    page: Optional[int] = None
    score: float
    snippet: Optional[str] = None


class SearchResponse(BaseModel):
    """RAG 검색 응답."""

    results: List[RagDocument]


class StatsResponse(BaseModel):
    """서버 통계."""

    search_call_count: int
    last_query: Optional[str]
    last_dataset: Optional[str]


# =============================================================================
# 글로벌 상태 (테스트 검증용)
# =============================================================================


class ServerState:
    """서버 상태 추적."""

    def __init__(self):
        self.search_call_count = 0
        self.last_query: Optional[str] = None
        self.last_dataset: Optional[str] = None

    def reset(self):
        self.search_call_count = 0
        self.last_query = None
        self.last_dataset = None


state = ServerState()


# =============================================================================
# Mock 데이터
# =============================================================================

# POLICY 도메인 샘플 문서
POLICY_DOCUMENTS = [
    RagDocument(
        doc_id="HR-001",
        title="연차휴가 관리 규정",
        page=12,
        score=0.92,
        snippet="연차휴가의 이월은 최대 10일을 초과할 수 없으며, 이월된 연차는 다음 해 6월 30일까지 사용해야 합니다.",
    ),
    RagDocument(
        doc_id="HR-002",
        title="복리후생 규정",
        page=5,
        score=0.85,
        snippet="직원은 연간 100만원 한도 내에서 자기계발비를 지원받을 수 있습니다.",
    ),
    RagDocument(
        doc_id="HR-003",
        title="출퇴근 관리 지침",
        page=3,
        score=0.78,
        snippet="유연근무제를 사용하는 직원은 코어타임(10:00~16:00) 동안 반드시 근무해야 합니다.",
    ),
]

# INCIDENT 도메인 샘플 문서
INCIDENT_DOCUMENTS = [
    RagDocument(
        doc_id="SEC-001",
        title="보안사고 대응 매뉴얼",
        page=8,
        score=0.89,
        snippet="보안사고 발생 시 즉시 정보보안팀(내선 1234)에 신고해야 합니다.",
    ),
]


# =============================================================================
# 엔드포인트
# =============================================================================


@app.get("/health")
async def health_check():
    """헬스체크 엔드포인트."""
    return {"status": "ok", "service": "mock-ragflow", "timestamp": datetime.now().isoformat()}


@app.post("/search", response_model=SearchResponse)
async def search_documents(request: SearchRequest):
    """
    RAG 문서 검색 엔드포인트.

    실제 RAGFlow API를 시뮬레이션합니다.
    dataset에 따라 다른 문서 세트를 반환합니다.
    """
    # 상태 업데이트
    state.search_call_count += 1
    state.last_query = request.query
    state.last_dataset = request.dataset

    logger.info(
        f"[Mock RAGFlow] Search called: query='{request.query}', "
        f"dataset={request.dataset}, top_k={request.top_k}"
    )

    # 쿼리에 특정 키워드가 없으면 빈 결과 반환 (fallback 테스트용)
    if "연차" not in request.query and "휴가" not in request.query and "규정" not in request.query:
        # 일반적인 질문은 빈 결과 반환
        if request.dataset == "POLICY":
            logger.info("[Mock RAGFlow] No matching documents found")
            return SearchResponse(results=[])

    # dataset에 따라 문서 선택
    if request.dataset == "INCIDENT":
        documents = INCIDENT_DOCUMENTS
    else:
        # 기본값 또는 POLICY
        documents = POLICY_DOCUMENTS

    # top_k 적용
    results = documents[: request.top_k]

    logger.info(f"[Mock RAGFlow] Returning {len(results)} documents")
    return SearchResponse(results=results)


@app.get("/stats", response_model=StatsResponse)
async def get_stats():
    """
    서버 통계 엔드포인트 (테스트 검증용).

    통합 테스트에서 RAGFlow가 호출되었는지 확인할 때 사용합니다.
    """
    return StatsResponse(
        search_call_count=state.search_call_count,
        last_query=state.last_query,
        last_dataset=state.last_dataset,
    )


@app.post("/stats/reset")
async def reset_stats():
    """
    통계 초기화 엔드포인트.

    테스트 간 상태 초기화에 사용합니다.
    """
    state.reset()
    logger.info("[Mock RAGFlow] Stats reset")
    return {"status": "ok", "message": "Stats reset"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
