"""
AI 로그 서비스 모듈 (AI Log Service Module)

채팅 요청 처리 후 AI 로그를 생성하고 백엔드로 전송하는 서비스입니다.
로그는 비동기로 전송되어 메인 응답 latency에 영향을 주지 않습니다.

주요 기능:
- ChatRequest, ChatResponse, 파이프라인 메타데이터로부터 AILogEntry 생성
- LOG 단계 PII 마스킹 적용 (question_masked, answer_masked)
- 백엔드 API로 로그 전송 (fire-and-forget 방식)
"""

import asyncio
from typing import Optional

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.ai_log import AILogEntry, AILogRequest, AILogResponse, to_backend_log_payload
from app.models.chat import ChatRequest, ChatResponse
from app.models.intent import MaskingStage, PiiMaskResult
from app.services.pii_service import PiiService

logger = get_logger(__name__)
settings = get_settings()


class AILogService:
    """
    AI 로그 서비스.

    채팅 파이프라인 완료 후 로그를 생성하고 백엔드로 전송합니다.
    PII 원문은 절대 저장하지 않으며, LOG 단계에서 강하게 마스킹된 텍스트만 저장합니다.

    Attributes:
        _pii_service: PII 마스킹 서비스 (LOG 단계 마스킹용)
        _backend_log_endpoint: 백엔드 로그 저장 API 엔드포인트

    Usage:
        log_service = AILogService()
        await log_service.send_log(log_entry)
    """

    def __init__(self, pii_service: Optional[PiiService] = None) -> None:
        """
        AILogService 초기화.

        Args:
            pii_service: PII 마스킹 서비스. None이면 새로 생성.
        """
        self._pii_service = pii_service or PiiService()

        # 백엔드 로그 엔드포인트 설정
        # Phase 9: backend_base_url 프로퍼티 사용 (mock/real 모드 자동 선택)
        # Phase 50: trailing slash 제거로 //api/ai-logs 중복 방지
        if settings.backend_base_url:
            base_url = settings.backend_base_url.rstrip("/")
            self._backend_log_endpoint = f"{base_url}/api/ai-logs"
        else:
            self._backend_log_endpoint = None

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
    ) -> AILogEntry:
        """
        채팅 요청/응답 및 파이프라인 메타데이터로부터 AILogEntry를 생성합니다.

        Args:
            request: 원본 ChatRequest
            response: 생성된 ChatResponse
            intent: 분류된 의도 (IntentType.value)
            domain: 보정된 도메인
            route: 라우팅 결과 (RouteType.value)
            has_pii_input: 입력에서 PII 검출 여부
            has_pii_output: 출력에서 PII 검출 여부
            rag_used: RAG 사용 여부
            rag_source_count: RAG 검색 결과 개수
            latency_ms: 전체 처리 시간 (ms)
            model_name: 사용된 LLM 모델명
            error_code: 에러 코드 (있으면)
            error_message: 에러 메시지 (있으면)
            turn_index: 세션 내 턴 인덱스
            question_masked: LOG 단계 마스킹된 질문 (이미 마스킹된 경우)
            answer_masked: LOG 단계 마스킹된 답변 (이미 마스킹된 경우)

        Returns:
            AILogEntry: 생성된 로그 엔트리
        """
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
        )

    async def mask_for_log(
        self,
        question: str,
        answer: str,
    ) -> tuple[str, str]:
        """
        LOG 단계에서 질문과 답변에 대해 강화된 PII 마스킹을 적용합니다.

        LOG 단계 마스킹은 저장용으로 더 강하게 적용될 수 있습니다.
        PII 원문은 절대 DB에 저장되지 않도록 합니다.

        Args:
            question: 사용자 질문 텍스트
            answer: LLM 답변 텍스트

        Returns:
            tuple[str, str]: (마스킹된 질문, 마스킹된 답변)
        """
        question_result = await self._pii_service.detect_and_mask(
            text=question,
            stage=MaskingStage.LOG,
        )
        answer_result = await self._pii_service.detect_and_mask(
            text=answer,
            stage=MaskingStage.LOG,
        )

        return question_result.masked_text, answer_result.masked_text

    async def send_log(self, log_entry: AILogEntry) -> bool:
        """
        AI 로그를 백엔드로 전송합니다.

        Fire-and-forget 방식으로 동작하며, 전송 실패해도 메인 로직에 영향을 주지 않습니다.
        BACKEND_BASE_URL이 설정되지 않은 경우 로컬 로그만 기록합니다.

        Args:
            log_entry: 전송할 AILogEntry

        Returns:
            bool: 전송 성공 여부
        """
        # 로컬 로그 기록 (항상)
        logger.info(
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
        )

        # 백엔드 URL이 없으면 로컬 로그만
        if not self._backend_log_endpoint:
            logger.debug("BACKEND_BASE_URL not configured, skipping remote log")
            return True

        # 백엔드로 전송 (camelCase JSON)
        try:
            client = get_async_http_client()

            # camelCase JSON payload 생성
            payload = to_backend_log_payload(log_entry)

            # 인증 헤더 설정 (있으면)
            headers = {}
            if settings.BACKEND_API_TOKEN:
                headers["Authorization"] = f"Bearer {settings.BACKEND_API_TOKEN}"

            response = await client.post(
                self._backend_log_endpoint,
                json=payload,
                headers=headers if headers else None,
                timeout=5.0,  # 로그 전송은 빠르게
            )

            if response.status_code == 200 or response.status_code == 201:
                logger.debug(
                    f"AI log sent successfully: session={log_entry.session_id}"
                )
                return True
            else:
                logger.warning(
                    f"AI log send failed: status={response.status_code}, "
                    f"session={log_entry.session_id}"
                )
                return False

        except Exception as e:
            # 로그 전송 실패는 경고만 하고 진행
            logger.warning(
                f"AI log send error: {e}, session={log_entry.session_id}"
            )
            return False

    async def send_log_async(self, log_entry: AILogEntry) -> None:
        """
        AI 로그를 백엔드로 비동기 전송합니다 (fire-and-forget).

        메인 응답과 독립적으로 백그라운드에서 실행됩니다.
        전송 실패해도 예외를 발생시키지 않습니다.

        Args:
            log_entry: 전송할 AILogEntry
        """
        try:
            await self.send_log(log_entry)
        except Exception as e:
            # 백그라운드 작업 실패는 로그만 남김
            logger.error(f"Background AI log send failed: {e}")
