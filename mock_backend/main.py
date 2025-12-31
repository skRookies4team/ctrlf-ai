"""
Mock Backend Server for Integration Testing

Phase 8: Docker Compose 환경에서 AI 로그 수집을 시뮬레이션하는 Mock 서버입니다.
Spring 백엔드의 /api/ai-logs 엔드포인트를 시뮬레이션합니다.

엔드포인트:
- POST /api/ai-logs: AI 로그 수집
- GET /api/ai-logs: 저장된 로그 조회 (테스트 검증용)
- GET /health: 헬스체크
- GET /stats: 호출 통계 (테스트 검증용)
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

# 로깅 설정
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Mock Backend Server",
    description="Integration test용 AI 로그 수집 시뮬레이션 서버",
    version="0.1.0",
)


# =============================================================================
# 모델 정의
# =============================================================================


class AILogEntry(BaseModel):
    """AI 로그 엔트리 (Phase 5에서 정의한 형식)."""

    session_id: str
    user_id: str
    user_role: Optional[str] = None
    department: Optional[str] = None
    domain: Optional[str] = None
    channel: Optional[str] = None
    intent: Optional[str] = None
    route: Optional[str] = None
    question_original: Optional[str] = None
    question_masked: Optional[str] = None
    answer_original: Optional[str] = None
    answer_masked: Optional[str] = None
    rag_used: bool = False
    rag_source_count: int = 0
    rag_sources: Optional[List[Dict[str, Any]]] = None
    has_pii_input: bool = False
    has_pii_output: bool = False
    pii_tags_input: Optional[List[Dict[str, Any]]] = None
    pii_tags_output: Optional[List[Dict[str, Any]]] = None
    llm_model: Optional[str] = None
    latency_ms: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None


class LogResponse(BaseModel):
    """로그 저장 응답."""

    status: str
    message: str
    log_id: Optional[str] = None


class StatsResponse(BaseModel):
    """서버 통계."""

    log_call_count: int
    total_logs_stored: int
    last_session_id: Optional[str]


# =============================================================================
# 글로벌 상태 (테스트 검증용)
# =============================================================================


class ServerState:
    """서버 상태 추적."""

    def __init__(self):
        self.log_call_count = 0
        self.logs: List[AILogEntry] = []
        self.last_session_id: Optional[str] = None

    def reset(self):
        self.log_call_count = 0
        self.logs = []
        self.last_session_id = None


state = ServerState()


# =============================================================================
# 엔드포인트
# =============================================================================


@app.get("/health")
async def health_check():
    """헬스체크 엔드포인트."""
    return {"status": "ok", "service": "mock-backend", "timestamp": datetime.now().isoformat()}


@app.post("/api/ai-logs", response_model=LogResponse)
async def create_ai_log(log_entry: AILogEntry):
    """
    AI 로그 저장 엔드포인트.

    Spring 백엔드의 /api/ai-logs를 시뮬레이션합니다.
    받은 로그를 메모리에 저장하고 콘솔에 출력합니다.
    """
    # 상태 업데이트
    state.log_call_count += 1
    state.last_session_id = log_entry.session_id
    state.logs.append(log_entry)

    logger.info(
        f"[Mock Backend] AI Log received: session_id={log_entry.session_id}, "
        f"intent={log_entry.intent}, route={log_entry.route}, "
        f"rag_used={log_entry.rag_used}, has_pii_input={log_entry.has_pii_input}"
    )

    # 로그 내용 일부 출력
    if log_entry.question_masked:
        logger.debug(f"[Mock Backend] Question (masked): {log_entry.question_masked[:100]}...")
    if log_entry.answer_masked:
        logger.debug(f"[Mock Backend] Answer (masked): {log_entry.answer_masked[:100]}...")

    # PII 원문이 포함되어 있는지 경고 (테스트 검증용)
    if log_entry.question_original:
        logger.warning(
            "[Mock Backend] WARNING: question_original contains raw data. "
            "Ensure PII is properly handled."
        )

    return LogResponse(
        status="ok",
        message="Log stored successfully",
        log_id=f"log-{state.log_call_count:04d}",
    )


@app.get("/api/ai-logs")
async def get_ai_logs(limit: int = 10):
    """
    저장된 AI 로그 조회 엔드포인트 (테스트 검증용).

    통합 테스트에서 로그가 정상적으로 저장되었는지 확인할 때 사용합니다.
    """
    logs = state.logs[-limit:] if limit > 0 else state.logs
    return {
        "status": "ok",
        "total_count": len(state.logs),
        "returned_count": len(logs),
        "logs": [log.model_dump() for log in logs],
    }


@app.get("/stats", response_model=StatsResponse)
async def get_stats():
    """
    서버 통계 엔드포인트 (테스트 검증용).

    통합 테스트에서 로그 API가 호출되었는지 확인할 때 사용합니다.
    """
    return StatsResponse(
        log_call_count=state.log_call_count,
        total_logs_stored=len(state.logs),
        last_session_id=state.last_session_id,
    )


@app.post("/stats/reset")
async def reset_stats():
    """
    통계 및 로그 초기화 엔드포인트.

    테스트 간 상태 초기화에 사용합니다.
    """
    state.reset()
    logger.info("[Mock Backend] Stats and logs reset")
    return {"status": "ok", "message": "Stats and logs reset"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8081)
