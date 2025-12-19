"""
RAG 디버그 로깅 유틸리티

환경변수 DEBUG_RAG=1 설정 시 RAG 파이프라인 디버그 로그를 출력합니다.

디버그 로그 이벤트:
1. route - 라우팅 결정 결과
2. retrieval_target - Milvus 검색 대상 정보
3. final_query - 최종 검색 쿼리
4. retrieval_top5 - 검색 결과 상위 5개

사용법:
    .env 파일에 DEBUG_RAG=1 추가
    또는
    $env:DEBUG_RAG="1"  # PowerShell
    python chat_cli.py

    from app.utils.debug_log import dbg
    dbg("route", request_id, intent="POLICY_QA", domain="POLICY")
"""

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional

# .env 파일에서 환경변수 로드 (dotenv 사용)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv 없으면 스킵


def _is_debug_rag_enabled() -> bool:
    """DEBUG_RAG 환경변수를 동적으로 체크합니다."""
    return os.getenv("DEBUG_RAG", "0") == "1"


def dbg(event: str, request_id: str, **fields: Any) -> None:
    """
    RAG 디버그 로그를 출력합니다.

    DEBUG_RAG=1 환경변수가 설정된 경우에만 출력됩니다.
    JSON 한 줄 형식으로 출력하며, 민감정보는 제한합니다.

    Args:
        event: 이벤트 이름 (route, retrieval_target, final_query, retrieval_top5)
        request_id: 요청 ID (uuid4)
        **fields: 추가 필드들

    Example:
        dbg("route", request_id,
            user_message="연차 관련해서 알려줘",
            intent="POLICY_QA",
            domain="POLICY",
            tool="RAG_INTERNAL",
            reason="키워드 '연차' 감지")
    """
    if not _is_debug_rag_enabled():
        return

    # 로그 데이터 구성
    log_data: Dict[str, Any] = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "event": event,
        "request_id": request_id,
    }

    # 필드 추가 (민감정보 제한)
    for key, value in fields.items():
        if value is not None:
            log_data[key] = _sanitize_value(key, value)

    # JSON 한 줄 출력 (ensure_ascii=False로 한글 유지)
    try:
        json_line = json.dumps(log_data, ensure_ascii=False, default=str)
        # stderr로 출력 (stdout은 응답용)
        print(f"[DEBUG_RAG] {json_line}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[DEBUG_RAG] Error logging: {e}", file=sys.stderr, flush=True)


def _sanitize_value(key: str, value: Any) -> Any:
    """
    민감정보 제한을 적용합니다.

    Args:
        key: 필드 키
        value: 필드 값

    Returns:
        제한된 값
    """
    # user_message, original_query, rewritten_query: 200자 제한
    if key in ("user_message", "original_query", "rewritten_query", "query"):
        if isinstance(value, str) and len(value) > 200:
            return value[:200] + "..."
        return value

    # text 필드: 로그에서 제외 (타이틀/ID/점수만)
    if key == "text" or key == "content" or key == "snippet":
        return "[REDACTED]"

    # results 리스트: 각 항목에서 본문 제거
    if key == "results" and isinstance(value, list):
        sanitized = []
        for item in value[:5]:  # 최대 5개
            if isinstance(item, dict):
                sanitized.append({
                    k: v for k, v in item.items()
                    if k not in ("text", "content", "snippet", "chunk_text")
                })
            else:
                sanitized.append(item)
        return sanitized

    return value


def generate_request_id() -> str:
    """
    새로운 request_id를 생성합니다.

    Returns:
        str: UUID4 형식의 request_id
    """
    import uuid
    return str(uuid.uuid4())


# =============================================================================
# Convenience Functions for Each Event Type
# =============================================================================


def dbg_route(
    request_id: str,
    user_message: str,
    intent: str,
    domain: str,
    tool: str,
    reason: Optional[str] = None,
    confidence: Optional[float] = None,
) -> None:
    """
    [A] 라우팅 결정 직후 로그.

    Args:
        request_id: 요청 ID
        user_message: 사용자 메시지 (200자 제한)
        intent: 분류된 의도
        domain: 분류된 도메인
        tool: 선택된 도구/라우트
        reason: 선택 이유 (선택)
        confidence: 신뢰도 점수 (선택)
    """
    dbg(
        "route",
        request_id,
        user_message=user_message,
        intent=intent,
        domain=domain,
        tool=tool,
        reason=reason,
        confidence=confidence,
    )


def dbg_retrieval_target(
    request_id: str,
    collection: str,
    partition: Optional[str] = None,
    filter_expr: Optional[str] = None,
    top_k: int = 5,
    domain: Optional[str] = None,
) -> None:
    """
    [B] Milvus 검색 직전 로그.

    Args:
        request_id: 요청 ID
        collection: Milvus 컬렉션 이름
        partition: 파티션 이름 (선택)
        filter_expr: 필터 표현식 (선택)
        top_k: 반환할 결과 수
        domain: 검색 도메인 (선택)
    """
    dbg(
        "retrieval_target",
        request_id,
        collection=collection,
        partition=partition,
        filter_expr=filter_expr,
        top_k=top_k,
        domain=domain,
    )


def dbg_final_query(
    request_id: str,
    original_query: str,
    rewritten_query: Optional[str] = None,
    keywords: Optional[list] = None,
) -> None:
    """
    [C] 최종 검색 질의 확정 직후 로그.

    Args:
        request_id: 요청 ID
        original_query: 원본 쿼리 (200자 제한)
        rewritten_query: 리라이트된 쿼리 (선택, 200자 제한)
        keywords: 추출된 키워드 리스트 (선택)
    """
    dbg(
        "final_query",
        request_id,
        original_query=original_query,
        rewritten_query=rewritten_query,
        keywords=keywords,
    )


def dbg_retrieval_top5(
    request_id: str,
    results: list,
) -> None:
    """
    [D] Milvus 결과 수신 직후 로그.

    Args:
        request_id: 요청 ID
        results: 상위 5개 결과 리스트
            각 항목: {doc_title, chunk_id, score_or_distance}
    """
    # 상위 5개만 추출
    top5 = results[:5] if results else []
    dbg(
        "retrieval_top5",
        request_id,
        count=len(results),
        results=top5,
    )


def is_debug_enabled() -> bool:
    """
    디버그 모드가 활성화되어 있는지 확인합니다.

    Returns:
        bool: DEBUG_RAG=1이면 True
    """
    return _is_debug_rag_enabled()
