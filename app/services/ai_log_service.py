"""
AI 로그 서비스 모듈 (AI Log Service Module)

- 채팅 요청 처리 후 AI 로그 생성
- LOG 단계 PII 마스킹 적용
- Elasticsearch에 직접 적재 (Single Source of Truth)

이 로그는:
- FAQ 자동 생성
- RAG Gap 분석
- 운영 모니터링
의 기준 데이터가 된다.
"""

import datetime
from typing import List, Optional, Any

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.ai_log import AILogEntry
from app.models.chat import ChatRequest, ChatResponse
from app.models.intent import MaskingStage
from app.services.pii_service import PiiService

logger = get_logger(__name__)
settings = get_settings()


# ==================================================
# UTF-8 안전화 (ES Boundary)
# ==================================================

def normalize_utf8(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value.encode("utf-8", errors="ignore").decode("utf-8")
    return value


def safe_es_doc(doc: dict) -> dict:
    return {k: normalize_utf8(v) for k, v in doc.items()}


# ==================================================
# AILogService
# ==================================================

class AILogService:
    """
    AI 로그 서비스 (Elasticsearch Direct Write)

    ❗ Elasticsearch = Single Source of Truth
    """

    def __init__(self, pii_service: Optional[PiiService] = None) -> None:
        self._pii_service = pii_service or PiiService()

        if not settings.ELASTICSEARCH_URL:
            raise RuntimeError("ELASTICSEARCH_URL is not configured")

        self._es_base_url = settings.ELASTICSEARCH_URL.rstrip("/")

        # 🔥 FAQ 강제 로그 (TEST MODE)
        self._force_faq_log = getattr(settings, "FAQ_LOG_FORCE", False)

        if self._force_faq_log:
            logger.warning("⚠️ FAQ_LOG_FORCE enabled (TEST MODE)")

    # ==================================================
    # Log Entry 생성
    # ==================================================

    def create_log_entry(
        self,
        request: ChatRequest,
        response: ChatResponse,
        intent: str,
        domain: str,
        route: str,
        has_pii_input: bool,
        has_pii_output: bool,
        rag_used: bool,
        rag_source_count: int,
        latency_ms: int,
        model_name: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        turn_index: Optional[int] = None,
        question_masked: Optional[str] = None,
        answer_masked: Optional[str] = None,
        rag_gap_candidate: bool = False,
        # 품질 분석용: 사용된 문서 ID 목록
        used_doc_ids: Optional[List[str]] = None,
        # Phase AB: A/B 테스트 정보
        ab_model: Optional[str] = None,
        ab_embedding_model: Optional[str] = None,
        ab_collection_name: Optional[str] = None,
    ) -> AILogEntry:
        return AILogEntry(
            session_id=request.session_id,
            user_id=request.user_id,
            turn_index=turn_index,
            channel=request.channel,
            user_role=request.user_role,
            department=request.department,
            domain=domain,
            intent=intent,
            route=route,
            has_pii_input=has_pii_input,
            has_pii_output=has_pii_output,
            model_name=model_name,
            rag_used=rag_used,
            rag_source_count=rag_source_count,
            latency_ms=latency_ms,
            error_code=error_code,
            error_message=error_message,
            question_masked=question_masked,
            answer_masked=answer_masked,
            rag_gap_candidate=rag_gap_candidate,
            # 품질 분석용: 사용된 문서 ID 목록 (항상 [] 보장)
            used_doc_ids=used_doc_ids if used_doc_ids is not None else [],
            # Phase AB: A/B 테스트 정보
            ab_model=ab_model,
            ab_embedding_model=ab_embedding_model,
            ab_collection_name=ab_collection_name,
        )

    # ==================================================
    # LOG 단계 PII 마스킹
    # ==================================================

    async def mask_for_log(self, question: str, answer: str) -> tuple[str, str]:
        q = await self._pii_service.detect_and_mask(
            text=question,
            stage=MaskingStage.LOG,
        )
        a = await self._pii_service.detect_and_mask(
            text=answer,
            stage=MaskingStage.LOG,
        )
        return q.masked_text, a.masked_text

    # ==================================================
    # ai_log 저장
    # ==================================================

    async def send_log(self, log_entry: AILogEntry) -> None:
        index_name = f"ctrlf-logs-{datetime.date.today():%Y.%m.%d}"

        doc = safe_es_doc({
            "@timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "log_type": "ai_log",
            "domain": log_entry.domain,
            "intent": log_entry.intent,
            "question_masked": log_entry.question_masked,
            "answer_masked": log_entry.answer_masked,
            "rag_used": log_entry.rag_used,
            "rag_source_count": log_entry.rag_source_count,
            "rag_gap_candidate": log_entry.rag_gap_candidate,
            "used_doc_ids": log_entry.used_doc_ids,  # 품질 분석용 문서 ID 목록
            "session_id": log_entry.session_id,
            "user_id": log_entry.user_id,
            "turn_index": log_entry.turn_index,
            "route": log_entry.route,
            "model_name": log_entry.model_name,
            "latency_ms": log_entry.latency_ms,
            "error_code": log_entry.error_code,
            "error_message": log_entry.error_message,
        })

        try:
            client = get_async_http_client()
            resp = await client.post(
                f"{self._es_base_url}/{index_name}/_doc",
                json=doc,
                timeout=2.0,
            )

            if resp.status_code not in (200, 201):
                logger.error(
                    f"[AI_LOG] ES failed | status={resp.status_code} | body={resp.text}"
                )

        except Exception:
            logger.exception("[AI_LOG] ES exception")

    # ==================================================
    # faq_log 저장
    # ==================================================

    async def send_faq_log(self, log_entry: AILogEntry) -> None:
        index_name = f"ctrlf-faq-log-{datetime.date.today():%Y.%m.%d}"

        doc = safe_es_doc({
            "@timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "log_type": "faq_log",
            "domain": log_entry.domain,
            "intent": log_entry.intent,
            "question_masked": log_entry.question_masked,
            "source": "ai",
        })
        
        
        # 로컬 로그 기록 (항상)
        # Phase AB: A/B 테스트 정보 추가
        ab_info = ""
        if log_entry.ab_model:
            ab_info = (
                f", ab_model={log_entry.ab_model}, "
                f"ab_embedding={log_entry.ab_embedding_model}, "
                f"ab_collection={log_entry.ab_collection_name}"
            )
        logger.info(
            f"[FAQ_LOG] SAVE | domain={doc['domain']} | intent={doc['intent']}"
            f"AI Log: session={log_entry.session_id}, "
            f"user={log_entry.user_id}, "
            f"intent={log_entry.intent}, "
            f"route={log_entry.route}, "
            f"domain={log_entry.domain}, "
            f"pii_input={log_entry.has_pii_input}, "
            f"pii_output={log_entry.has_pii_output}, "
            f"rag_used={log_entry.rag_used}, "
            f"rag_sources={log_entry.rag_source_count}, "
            f"latency_ms={log_entry.latency_ms}"
            f"{ab_info}"
        )

        try:
            client = get_async_http_client()
            resp = await client.post(
                f"{self._es_base_url}/{index_name}/_doc",
                json=doc,
                timeout=2.0,
            )

            if resp.status_code not in (200, 201):
                logger.error(
                    f"[FAQ_LOG] ES failed | status={resp.status_code} | body={resp.text}"
                )

        except Exception:
            logger.exception("[FAQ_LOG] ES exception")

    # ==================================================
    # fire-and-forget
    # ==================================================

    async def send_log_async(self, log_entry: AILogEntry) -> None:
        try:
            # 1️⃣ 운영 로그 (항상 저장)
            await self.send_log(log_entry)

            # 2️⃣ FAQ 로그 (🔥 테스트 단계: 무조건 저장)
            logger.warning(
                f"[FAQ_LOG] FORCE SAVE | domain={log_entry.domain} | intent={log_entry.intent}"
            )
            await self.send_faq_log(log_entry)

        except Exception:
            logger.exception("[AI_LOG] background task failed")

