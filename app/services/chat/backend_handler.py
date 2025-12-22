"""
백엔드 데이터 핸들러 (Backend Data Handler)

ChatService에서 사용하는 백엔드 데이터 조회 로직을 담당합니다.

Phase 2 리팩토링:
- ChatService._fetch_backend_data_for_api → BackendHandler.fetch_for_api
- ChatService._fetch_backend_data_for_mixed → BackendHandler.fetch_for_mixed

역할×도메인×의도 매핑:
- BACKEND_API: 직접 백엔드 데이터만 사용
- MIXED_BACKEND_RAG: RAG + 백엔드 데이터 조합
"""

from typing import Optional

from app.clients.backend_client import BackendDataClient
from app.core.logging import get_logger
from app.models.intent import IntentType, UserRole
from app.services.backend_context_formatter import BackendContextFormatter

logger = get_logger(__name__)


class BackendHandler:
    """
    백엔드 데이터 조회를 처리하는 핸들러 클래스.

    역할×도메인×의도 조합에 따라 적절한 BackendDataClient 메서드를 호출하고,
    결과를 LLM 컨텍스트 텍스트로 변환합니다.

    Attributes:
        _backend_data: BackendDataClient 인스턴스
        _context_formatter: BackendContextFormatter 인스턴스
    """

    def __init__(
        self,
        backend_data_client: BackendDataClient,
        context_formatter: BackendContextFormatter,
    ) -> None:
        """
        BackendHandler 초기화.

        Args:
            backend_data_client: BackendDataClient 인스턴스
            context_formatter: BackendContextFormatter 인스턴스
        """
        self._backend_data = backend_data_client
        self._context_formatter = context_formatter

    async def fetch_for_api(
        self,
        user_role: UserRole,
        domain: str,
        intent: IntentType,
        user_id: str,
        department: Optional[str] = None,
    ) -> str:
        """
        BACKEND_API 라우트용 백엔드 데이터를 조회합니다.

        역할×도메인×의도 조합에 따라 적절한 BackendDataClient 메서드를 호출하고,
        결과를 LLM 컨텍스트 텍스트로 변환합니다.

        Args:
            user_role: 사용자 역할
            domain: 도메인
            intent: 의도
            user_id: 사용자 ID
            department: 부서 ID

        Returns:
            str: LLM 컨텍스트용 텍스트

        Phase 11 역할×도메인×의도 매핑:
        - EMPLOYEE × EDU_STATUS → get_employee_edu_status(user_id)
        - EMPLOYEE × INCIDENT_REPORT → get_report_guide()
        - ADMIN × EDU_STATUS → get_department_edu_stats(department)
        """
        try:
            # EMPLOYEE × EDU_STATUS: 본인 교육 현황
            if user_role == UserRole.EMPLOYEE and intent == IntentType.EDU_STATUS:
                response = await self._backend_data.get_employee_edu_status(user_id)
                if response.success:
                    return self._context_formatter.format_edu_status_for_llm(
                        response.data
                    )

            # EMPLOYEE × INCIDENT_REPORT: 신고 안내
            elif user_role == UserRole.EMPLOYEE and intent == IntentType.INCIDENT_REPORT:
                response = await self._backend_data.get_report_guide()
                if response.success:
                    return self._context_formatter.format_report_guide_for_llm(
                        response.data
                    )

            # ADMIN × EDU_STATUS: 부서 교육 통계
            elif user_role == UserRole.ADMIN and intent == IntentType.EDU_STATUS:
                response = await self._backend_data.get_department_edu_stats(department)
                if response.success:
                    return self._context_formatter.format_edu_stats_for_llm(
                        response.data
                    )

            # 기타 조합: 데이터 없음
            logger.debug(
                f"No backend data mapping for: role={user_role.value}, "
                f"domain={domain}, intent={intent.value}"
            )
            return ""

        except Exception as e:
            logger.warning(f"Backend data fetch failed: {e}")
            return ""

    async def fetch_for_mixed(
        self,
        user_role: UserRole,
        domain: str,
        intent: IntentType,
        user_id: str,
        department: Optional[str] = None,
    ) -> str:
        """
        MIXED_BACKEND_RAG 라우트용 백엔드 데이터를 조회합니다.

        Args:
            user_role: 사용자 역할
            domain: 도메인
            intent: 의도
            user_id: 사용자 ID
            department: 부서 ID

        Returns:
            str: LLM 컨텍스트용 텍스트

        Phase 11 역할×도메인×의도 매핑:
        - ADMIN × INCIDENT → get_incident_overview()
        - ADMIN × EDU_STATUS → get_department_edu_stats()
        - INCIDENT_MANAGER × INCIDENT → get_incident_overview()
        """
        try:
            # ADMIN × INCIDENT: 사고 현황 통계
            if user_role == UserRole.ADMIN and domain == "INCIDENT":
                response = await self._backend_data.get_incident_overview()
                if response.success:
                    return self._context_formatter.format_incident_overview_for_llm(
                        response.data
                    )

            # ADMIN × EDU_STATUS: 부서 교육 통계 (MIXED에서도 사용 가능)
            elif user_role == UserRole.ADMIN and intent == IntentType.EDU_STATUS:
                response = await self._backend_data.get_department_edu_stats(department)
                if response.success:
                    return self._context_formatter.format_edu_stats_for_llm(
                        response.data
                    )

            # INCIDENT_MANAGER × INCIDENT: 사고 현황
            elif user_role == UserRole.INCIDENT_MANAGER and domain == "INCIDENT":
                response = await self._backend_data.get_incident_overview()
                if response.success:
                    return self._context_formatter.format_incident_overview_for_llm(
                        response.data
                    )

            # 기타 조합: 데이터 없음
            logger.debug(
                f"No mixed backend data mapping for: role={user_role.value}, "
                f"domain={domain}, intent={intent.value}"
            )
            return ""

        except Exception as e:
            logger.warning(f"Mixed backend data fetch failed: {e}")
            return ""
