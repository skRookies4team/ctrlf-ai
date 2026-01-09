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
from typing import Any, Dict, List, Optional

import httpx
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
    """
    Elasticsearch 문서를 안전하게 정규화합니다.
    
    createdAt 필드는 항상 ISO 8601 형식(밀리초 포함) 문자열로 보장됩니다.
    프론트엔드 대시보드의 toLocaleString() 호출 시 null 에러를 방지합니다.
    """
    result = {k: normalize_utf8(v) for k, v in doc.items()}
    
    # createdAt 필드가 없거나 None이면 @timestamp를 사용 (fallback)
    if not result.get("createdAt") and result.get("@timestamp"):
        result["createdAt"] = result["@timestamp"]
    
    # createdAt 필드가 여전히 없으면 현재 시간 사용 (최종 fallback)
    if not result.get("createdAt"):
        # ISO 8601 형식 (밀리초 포함): "2026-01-09T17:27:40.800Z"
        now = datetime.datetime.utcnow()
        result["createdAt"] = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    # createdAt이 유효한 ISO 8601 형식 문자열인지 검증 및 정규화
    createdAt_value = result.get("createdAt")
    if createdAt_value:
        try:
            # 문자열인지 확인
            if isinstance(createdAt_value, str):
                # ISO 8601 형식 검증 및 정규화
                # "Z" 접미사가 있으면 그대로, 없으면 추가
                if not createdAt_value.endswith("Z") and not createdAt_value.endswith("+00:00"):
                    # datetime 파싱 시도
                    try:
                        dt = datetime.datetime.fromisoformat(createdAt_value.replace("Z", "+00:00"))
                        # ISO 8601 형식으로 변환 (밀리초 포함)
                        result["createdAt"] = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                    except (ValueError, AttributeError):
                        # 파싱 실패 시 현재 시간 사용
                        now = datetime.datetime.utcnow()
                        result["createdAt"] = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                elif createdAt_value.endswith("Z"):
                    # "Z" 형식이지만 밀리초가 없을 수 있음 - 그대로 유지 (백엔드가 처리)
                    pass
            else:
                # datetime 객체인 경우 ISO 8601 문자열로 변환
                if isinstance(createdAt_value, datetime.datetime):
                    result["createdAt"] = createdAt_value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                else:
                    # 기타 타입은 현재 시간 사용
                    now = datetime.datetime.utcnow()
                    result["createdAt"] = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        except Exception:
            # 모든 변환 실패 시 현재 시간 사용
            now = datetime.datetime.utcnow()
            result["createdAt"] = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    # @timestamp도 동일하게 보장 (createdAt과 동일한 값)
    if not result.get("@timestamp") and result.get("createdAt"):
        result["@timestamp"] = result["createdAt"]
    
    return result


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
        self._settings = get_settings()

        if not self._settings.ELASTICSEARCH_URL:
            raise RuntimeError("ELASTICSEARCH_URL is not configured")

        self._es_base_url = self._settings.ELASTICSEARCH_URL.rstrip("/")

        # 백엔드 실시간 전송 설정
        self._backend_url = (
            str(self._settings.BACKEND_INFRA_URL).rstrip("/")
            if self._settings.BACKEND_INFRA_URL
            else None
        )
        self._internal_token = self._settings.BACKEND_INTERNAL_TOKEN

        # 🔥 FAQ 강제 로그 (TEST MODE)
        self._force_faq_log = getattr(self._settings, "FAQ_LOG_FORCE", False)

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
        # chat_log: 관리자 대시보드용 (Elasticsearch 저장)
        # 백엔드가 관리하는 chat_log 인덱스에 직접 저장 (관리자 대시보드 실시간 조회용)
        # PostgreSQL infra.ai_log 테이블에도 실시간 전송 (_push_to_backend_realtime)
        index_name = "chat_log"  # 백엔드가 관리하는 chat_log 인덱스에 직접 저장
        # ISO 8601 형식 (밀리초 포함): "2026-01-09T17:27:40.800Z"
        now = datetime.datetime.utcnow()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        doc = safe_es_doc({
            "@timestamp": timestamp,
            "createdAt": timestamp,  # 백엔드 chat_log 인덱스 구조에 맞춤
            "role": "user",  # 백엔드 chat_log 인덱스 구조에 맞춤 (사용자 질문)
            "content": log_entry.question_masked,  # 백엔드 chat_log 인덱스 구조에 맞춤 (질문 내용)
            "questionMasked": log_entry.question_masked,  # 프론트엔드 대시보드용 (camelCase)
            "question_masked": log_entry.question_masked,  # snake_case 호환성
            "answerMasked": log_entry.answer_masked,  # 프론트엔드 대시보드용 (camelCase)
            "answer_masked": log_entry.answer_masked,  # snake_case 호환성
            "domain": log_entry.domain,
            "intent": log_entry.intent,  # 프론트엔드 대시보드용
            "route": log_entry.route,
            "userId": log_entry.user_id,  # 백엔드 chat_log 인덱스 구조에 맞춤 (camelCase)
            "user_id": log_entry.user_id,  # snake_case 호환성
            "sessionId": log_entry.session_id,  # 백엔드 chat_log 인덱스 구조에 맞춤 (camelCase)
            "session_id": log_entry.session_id,  # snake_case 호환성
            "userRole": log_entry.user_role,  # camelCase
            "user_role": log_entry.user_role,  # snake_case 호환성
            "department": log_entry.department,
            "turn_index": log_entry.turn_index,
            "model_name": log_entry.model_name,
            "modelName": log_entry.model_name,  # camelCase 호환성
            "latency_ms": log_entry.latency_ms,
            "latencyMs": log_entry.latency_ms,  # camelCase 호환성
            "rag_used": log_entry.rag_used,
            "ragUsed": log_entry.rag_used,  # camelCase 호환성
            "rag_source_count": log_entry.rag_source_count,
            "ragSourceCount": log_entry.rag_source_count,  # camelCase 호환성
            "rag_gap_candidate": log_entry.rag_gap_candidate,
            "ragGapCandidate": log_entry.rag_gap_candidate,  # camelCase 호환성
            "used_doc_ids": log_entry.used_doc_ids,  # 품질 분석용 문서 ID 목록
            "usedDocIds": log_entry.used_doc_ids,  # camelCase 호환성
            "error_code": log_entry.error_code,
            "errorCode": log_entry.error_code,  # camelCase 호환성
            "error_message": log_entry.error_message,
            "errorMessage": log_entry.error_message,  # 프론트엔드 대시보드용 (camelCase)
            "source": "ai",  # AI 서버에서 저장한 로그임을 표시
        })

        try:
            client = get_async_http_client()
            resp = await client.post(
                f"{self._es_base_url}/{index_name}/_doc",
                json=doc,
                timeout=2.0,
            )

            if resp.status_code in (200, 201):
                logger.info(
                    f"[AI_LOG] ✅ Saved successfully | index={index_name} | "
                    f"user_id={log_entry.user_id} | session_id={log_entry.session_id} | "
                    f"domain={log_entry.domain} | route={log_entry.route} | "
                    f"timestamp={timestamp}"
                )
            else:
                logger.error(
                    f"[AI_LOG] ❌ ES failed | index={index_name} | "
                    f"status={resp.status_code} | user_id={log_entry.user_id} | "
                    f"session_id={log_entry.session_id} | body={resp.text[:500]}"
                )

        except Exception as e:
            logger.exception(
                f"[AI_LOG] ❌ ES exception | index={index_name} | "
                f"user_id={log_entry.user_id} | session_id={log_entry.session_id} | "
                f"error={str(e)}"
            )

    # ==================================================
    # faq_log 저장
    # ==================================================

    async def send_faq_log(self, log_entry: AILogEntry) -> None:
        # faq_log: FAQ 자동 생성용 (Elasticsearch에서 직접 조회)
        index_name = f"ctrlf-faq-log-{datetime.date.today():%Y.%m.%d}"
        # ISO 8601 형식 (밀리초 포함): "2026-01-09T17:27:40.800Z"
        now = datetime.datetime.utcnow()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        doc = safe_es_doc({
            "@timestamp": timestamp,
            "log_type": "faq_log",  # FAQ 자동 생성용 로그
            "domain": log_entry.domain,
            "intent": log_entry.intent,
            "question_masked": log_entry.question_masked,
            "user_id": log_entry.user_id,  # FAQ 자동 생성을 위한 사용자 ID
            "session_id": log_entry.session_id,  # FAQ 자동 생성을 위한 세션 ID
            "role": "user",  # 백엔드 필수 필드: 항상 "user" (사용자 질문만 저장)
            "createdAt": timestamp,  # 백엔드 기대 필드: createdAt (ISO 8601)
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

            if resp.status_code in (200, 201):
                logger.info(
                    f"[FAQ_LOG] ✅ Saved successfully | index={index_name} | "
                    f"user_id={log_entry.user_id} | session_id={log_entry.session_id} | "
                    f"domain={log_entry.domain} | question_masked={bool(log_entry.question_masked)}"
                )
            else:
                logger.error(
                    f"[FAQ_LOG] ❌ ES failed | index={index_name} | "
                    f"status={resp.status_code} | user_id={log_entry.user_id} | "
                    f"session_id={log_entry.session_id} | body={resp.text[:200]}"
                )

        except Exception as e:
            logger.exception(
                f"[FAQ_LOG] ❌ ES exception | index={index_name} | "
                f"user_id={log_entry.user_id} | session_id={log_entry.session_id} | "
                f"error={str(e)}"
            )

    # ==================================================
    # Backend DTO 변환 및 실시간 전송
    # ==================================================

    def _convert_to_backend_dto(self, log_entry: AILogEntry) -> Dict[str, Any]:
        """
        AILogEntry를 Backend DTO 스키마로 변환합니다.

        Args:
            log_entry: AI 로그 엔트리

        Returns:
            Backend DTO 형식의 딕셔너리
        """
        # ISO 8601 형식 (밀리초 포함): "2026-01-09T17:27:40.800Z"
        now = datetime.datetime.utcnow()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        return {
            # 필수
            "createdAt": timestamp,  # 항상 유효한 ISO 8601 형식 문자열 보장
            "userId": log_entry.user_id,
            # 선택
            "userRole": log_entry.user_role,
            "department": log_entry.department,
            "domain": log_entry.domain,
            "intent": log_entry.intent,  # 프론트 대시보드용: 의도 분류
            "route": log_entry.route,
            "modelName": log_entry.model_name,
            "hasPiiInput": log_entry.has_pii_input or False,
            "hasPiiOutput": log_entry.has_pii_output or False,
            "ragUsed": log_entry.rag_used or False,
            "ragSourceCount": log_entry.rag_source_count or 0,
            "latencyMsTotal": log_entry.latency_ms,
            "errorCode": log_entry.error_code,
            "errorMessage": log_entry.error_message,  # 프론트 대시보드용: 에러 메시지
            # 프론트 대시보드용: 질문/답변 내용 (PII 마스킹된 텍스트)
            "questionMasked": log_entry.question_masked,
            "answerMasked": log_entry.answer_masked,
            # Trace
            "traceId": None,  # TODO: trace_id 필드 추가 필요
            "conversationId": log_entry.session_id,
            "turnId": log_entry.turn_index,
        }

    async def _push_to_backend_realtime(self, log_entry: AILogEntry) -> None:
        """
        백엔드로 로그를 실시간 전송합니다 (fire-and-forget).

        실시간 전송 실패 시 LogSyncService가 주기적으로 복구합니다.
        백엔드에서 중복 체크 (traceId + conversationId + turnId)를 수행하므로
        실시간 전송과 주기적 동기화가 동시에 실행되어도 안전합니다.

        Args:
            log_entry: AI 로그 엔트리
        """
        if not self._backend_url or not self._internal_token:
            # 백엔드 설정이 없으면 실시간 전송 스킵 (LogSyncService가 처리)
            logger.debug(
                f"[AI_LOG] 백엔드 실시간 전송 스킵: "
                f"BACKEND_INFRA_URL={bool(self._backend_url)}, "
                f"BACKEND_INTERNAL_TOKEN={bool(self._internal_token)} "
                f"(LogSyncService가 주기적으로 처리합니다)"
            )
            return

        try:
            # Backend DTO 형식으로 변환
            backend_dto = self._convert_to_backend_dto(log_entry)
            payload = {"logs": [backend_dto]}

            headers = {
                "Content-Type": "application/json",
                "X-Internal-Token": self._internal_token,
            }

            # 실시간 전송 (타임아웃 짧게 설정하여 응답 지연 방지)
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.post(
                    f"{self._backend_url}/internal/ai/logs/bulk",
                    json=payload,
                    headers=headers,
                )

                if resp.status_code == 200:
                    # 성공 시 응답 확인
                    try:
                        response_data = resp.json()
                        saved = response_data.get("saved", 0)
                        skipped = response_data.get("skipped", 0)
                        failed = response_data.get("failed", 0)
                        logger.info(
                            f"[AI_LOG] ✅ Real-time backend push succeeded | "
                            f"user_id={log_entry.user_id} | session_id={log_entry.session_id} | "
                            f"saved={saved}, skipped={skipped}, failed={failed}"
                        )
                    except Exception:
                        logger.info(
                            f"[AI_LOG] ✅ Real-time backend push succeeded | "
                            f"user_id={log_entry.user_id} | session_id={log_entry.session_id}"
                        )
                else:
                    # 실패 시 경고 (LogSyncService가 복구)
                    logger.warning(
                        f"[AI_LOG] ⚠️ Real-time backend push failed (will retry via LogSyncService) | "
                        f"status={resp.status_code} | user_id={log_entry.user_id} | "
                        f"session_id={log_entry.session_id} | url={self._backend_url}/internal/ai/logs/bulk | "
                        f"response={resp.text[:300]}"
                    )

        except httpx.TimeoutException:
            # 타임아웃은 경고만 (LogSyncService가 복구)
            logger.debug(
                f"[AI_LOG] ⚠️ Real-time backend push timeout (will retry via LogSyncService) | "
                f"user_id={log_entry.user_id} | session_id={log_entry.session_id}"
            )
        except Exception as e:
            # 기타 에러는 경고만 (LogSyncService가 복구)
            logger.debug(
                f"[AI_LOG] ⚠️ Real-time backend push error (will retry via LogSyncService) | "
                f"user_id={log_entry.user_id} | session_id={log_entry.session_id} | "
                f"error={type(e).__name__}: {str(e)[:100]}"
            )

    # ==================================================
    # fire-and-forget
    # ==================================================

    async def send_log_async(self, log_entry: AILogEntry) -> None:
        """
        로그를 비동기로 저장 및 전송합니다 (fire-and-forget).

        1. chat_log: Elasticsearch 저장 + PostgreSQL 동기화 (관리자 대시보드용)
           - Elasticsearch에 저장 (chat_log 인덱스, 백엔드가 관리하는 인덱스에 직접 저장)
           - PostgreSQL infra.ai_log에도 실시간 전송 (관리자 대시보드에서 조회, 백업)
           - LogSyncService가 주기적으로 동기화 (백업)
        2. faq_log: Elasticsearch 저장만 (FAQ 자동 생성용, user_id, session_id 포함)
           - FAQ 자동 생성 시 Elasticsearch chat_log 인덱스에서 직접 조회
        """
        try:
            # 1️⃣ chat_log: 관리자 대시보드용 로그
            # Elasticsearch 저장 (백엔드가 관리하는 chat_log 인덱스에 직접 저장)
            await self.send_log(log_entry)  # Elasticsearch: chat_log (백엔드 인덱스)
            # PostgreSQL infra.ai_log에 실시간 전송 (관리자 대시보드에서 조회, 백업)
            await self._push_to_backend_realtime(log_entry)

            # 2️⃣ faq_log: FAQ 자동 생성용 로그
            # Elasticsearch에만 저장 (FAQ 생성 시 직접 조회)
            await self.send_faq_log(log_entry)

        except Exception as e:
            logger.exception(
                f"[AI_LOG] ❌ background task failed | "
                f"user_id={log_entry.user_id} | session_id={log_entry.session_id} | "
                f"error={str(e)}"
            )

