"""
백엔드 비즈니스 데이터 클라이언트 (Backend Data Client)

ctrlf-back (Spring 백엔드)에서 비즈니스 데이터를 조회하는 HTTP 클라이언트입니다.
BACKEND_API / MIXED_BACKEND_RAG 라우트에서 사용됩니다.

Phase 11 신규 추가:
- 교육 현황/통계 조회 (EDU 도메인)
- 사고/위반 통계/상세 조회 (INCIDENT 도메인)
- 신고 플로우 안내 (INCIDENT_REPORT)

Phase 12 업데이트:
- UpstreamServiceError로 에러 래핑 (옵션: raise_on_error)
- 재시도 로직 추가 (1회)
- 개별 latency 측정

⚠️ 현재 상태:
- 백엔드 API 스펙이 100% 확정되지 않아 엔드포인트/필드는 설정으로 교체 가능한 형태
- 실제 연동 전까지는 테스트용 mock 응답 기준으로 동작
- TODO 주석으로 확정 후 수정 필요한 부분 표시

사용 방법:
    from app.clients.backend_data_client import BackendDataClient

    client = BackendDataClient()
    edu_status = await client.get_employee_edu_status(user_id="user-123")
"""

import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.exceptions import ErrorType, ServiceType, UpstreamServiceError
from app.core.logging import get_logger
from app.core.retry import BACKEND_RETRY_CONFIG, DEFAULT_BACKEND_TIMEOUT, retry_async_operation

logger = get_logger(__name__)
settings = get_settings()


# =============================================================================
# API 엔드포인트 경로 상수 (백엔드 스펙 확정 시 수정)
# =============================================================================

# 교육(EDU) 도메인 엔드포인트
# TODO: 백엔드 API 스펙 확정 후 실제 경로로 변경
BACKEND_EDU_STATUS_PATH = "/api/edu/status"  # 직원 본인 교육 현황
BACKEND_EDU_STATS_PATH = "/api/edu/stats"  # 부서/전체 교육 통계 (관리자용)

# 사고/위반(INCIDENT) 도메인 엔드포인트
# TODO: 백엔드 API 스펙 확정 후 실제 경로로 변경
BACKEND_INCIDENT_OVERVIEW_PATH = "/api/incidents/overview"  # 사고 통계 요약
BACKEND_INCIDENT_DETAIL_PATH = "/api/incidents/{incident_id}"  # 사건 상세

# 신고 플로우 엔드포인트
# TODO: 백엔드 API 스펙 확정 후 실제 경로로 변경
BACKEND_REPORT_GUIDE_PATH = "/api/incidents/report-guide"  # 신고 안내 정보


# =============================================================================
# 응답 데이터 타입 정의 (백엔드 스펙 확정 시 Pydantic 모델로 변경 가능)
# =============================================================================

class BackendDataResponse:
    """백엔드 데이터 조회 응답.

    Attributes:
        success: 조회 성공 여부
        data: 조회된 데이터 (dict 형태)
        error_message: 에러 시 메시지
    """

    def __init__(
        self,
        success: bool,
        data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ):
        self.success = success
        self.data = data or {}
        self.error_message = error_message


# =============================================================================
# BackendDataClient 클래스
# =============================================================================

class BackendDataClient:
    """
    백엔드 비즈니스 데이터 클라이언트.

    BACKEND_API / MIXED_BACKEND_RAG 라우트에서 사용되는 비즈니스 데이터를
    ctrlf-back (Spring 백엔드)에서 조회합니다.

    Attributes:
        _base_url: 백엔드 서비스 base URL
        _api_token: API 인증 토큰 (선택사항)
        _timeout: HTTP 요청 타임아웃 (초)

    Usage:
        client = BackendDataClient()

        # 직원 교육 현황 조회
        edu_status = await client.get_employee_edu_status("user-123")

        # 관리자용 부서 교육 통계
        dept_stats = await client.get_department_edu_stats("dept-001")

        # 사고 현황 요약
        incident_overview = await client.get_incident_overview()

    Note:
        - 백엔드 URL 미설정 시 mock 응답 반환
        - 백엔드 API 스펙 확정 후 엔드포인트/필드만 교체하면 됨
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout: float = DEFAULT_BACKEND_TIMEOUT,
    ) -> None:
        """
        BackendDataClient 초기화.

        Args:
            base_url: 백엔드 서비스 URL. None이면 설정에서 가져옴.
            api_token: API 인증 토큰. None이면 설정에서 가져옴.
            timeout: HTTP 요청 타임아웃 (초). 기본 5초.
        """
        self._base_url = base_url or settings.backend_base_url
        self._api_token = api_token if api_token is not None else settings.BACKEND_API_TOKEN
        self._timeout = timeout
        # Phase 12: 마지막 호출 latency 기록용 (모니터링)
        self._last_latency_ms: Optional[int] = None

    @property
    def is_configured(self) -> bool:
        """백엔드 URL이 설정되었는지 확인."""
        return bool(self._base_url)

    def _get_auth_headers(self) -> Dict[str, str]:
        """인증 헤더를 반환합니다."""
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    # =========================================================================
    # 교육(EDU) 도메인 메서드
    # =========================================================================

    async def get_employee_edu_status(
        self,
        user_id: str,
        year: Optional[int] = None,
    ) -> BackendDataResponse:
        """
        직원 본인의 교육 수료 현황/기한을 조회합니다.

        EMPLOYEE × EDU_STATUS 라우트에서 사용됩니다.

        Args:
            user_id: 사용자 ID
            year: 조회 연도 (기본: 현재 연도)

        Returns:
            BackendDataResponse: 교육 현황 데이터

        Expected response data structure:
            {
                "user_id": "user-123",
                "total_required": 4,
                "completed": 3,
                "pending": 1,
                "courses": [
                    {"name": "정보보호교육", "status": "completed", "completed_at": "2025-03-15"},
                    {"name": "개인정보보호교육", "status": "completed", "completed_at": "2025-04-20"},
                    {"name": "직장 내 괴롭힘 방지", "status": "completed", "completed_at": "2025-05-10"},
                    {"name": "산업안전보건", "status": "pending", "deadline": "2025-12-31"}
                ],
                "next_deadline": "2025-12-31"
            }

        TODO: 백엔드 API 스펙 확정 후 실제 필드명으로 변경
        """
        if not self.is_configured:
            logger.debug("Backend URL not configured, returning mock edu status")
            return self._mock_employee_edu_status(user_id)

        endpoint = f"{self._base_url}{BACKEND_EDU_STATUS_PATH}"
        params: Dict[str, Any] = {"userId": user_id}
        if year:
            params["year"] = year

        return await self._get_request(endpoint, params)

    async def get_department_edu_stats(
        self,
        department_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> BackendDataResponse:
        """
        관리자용 부서별/전체 교육 통계를 조회합니다.

        ADMIN × EDU_STATUS 라우트에서 사용됩니다.

        Args:
            department_id: 부서 ID (None이면 전체 통계)
            filters: 추가 필터 (연도, 교육 유형 등)

        Returns:
            BackendDataResponse: 교육 통계 데이터

        Expected response data structure:
            {
                "department_id": "dept-001",
                "department_name": "개발팀",
                "total_employees": 50,
                "completion_rate": 85.0,
                "by_course": [
                    {"name": "정보보호교육", "completed": 45, "pending": 5},
                    {"name": "개인정보보호교육", "completed": 42, "pending": 8}
                ],
                "pending_employees": [
                    {"user_id": "user-456", "name": "홍길동", "pending_courses": 2}
                ]
            }

        TODO: 백엔드 API 스펙 확정 후 실제 필드명으로 변경
        """
        if not self.is_configured:
            logger.debug("Backend URL not configured, returning mock dept edu stats")
            return self._mock_department_edu_stats(department_id)

        endpoint = f"{self._base_url}{BACKEND_EDU_STATS_PATH}"
        params: Dict[str, Any] = {}
        if department_id:
            params["departmentId"] = department_id
        if filters:
            params.update(filters)

        return await self._get_request(endpoint, params)

    # =========================================================================
    # 사고/위반(INCIDENT) 도메인 메서드
    # =========================================================================

    async def get_incident_overview(
        self,
        filters: Optional[Dict[str, Any]] = None,
    ) -> BackendDataResponse:
        """
        관리자/신고관리자용 사고/위반 요약 통계를 조회합니다.

        ADMIN × INCIDENT / INCIDENT_MANAGER × INCIDENT 라우트에서 사용됩니다.

        Args:
            filters: 필터 조건 (기간, 유형, 상태 등)
                - period: "month" | "quarter" | "year"
                - status: "open" | "closed" | "all"
                - type: "security" | "privacy" | "all"

        Returns:
            BackendDataResponse: 사고 통계 데이터

        Expected response data structure:
            {
                "period": "2025-Q4",
                "total_incidents": 15,
                "by_status": {
                    "open": 3,
                    "in_progress": 5,
                    "closed": 7
                },
                "by_type": {
                    "security": 8,
                    "privacy": 5,
                    "compliance": 2
                },
                "trend": {
                    "previous_period": 12,
                    "change_rate": 25.0
                }
            }

        TODO: 백엔드 API 스펙 확정 후 실제 필드명으로 변경
        """
        if not self.is_configured:
            logger.debug("Backend URL not configured, returning mock incident overview")
            return self._mock_incident_overview(filters)

        endpoint = f"{self._base_url}{BACKEND_INCIDENT_OVERVIEW_PATH}"
        params = filters or {}

        return await self._get_request(endpoint, params)

    async def get_incident_detail(
        self,
        incident_id: str,
    ) -> BackendDataResponse:
        """
        신고관리자용 특정 사건 상세 요약을 조회합니다.

        INCIDENT_MANAGER × INCIDENT_QA 라우트에서 사용됩니다.

        Args:
            incident_id: 사건 ID

        Returns:
            BackendDataResponse: 사건 상세 데이터

        Expected response data structure:
            {
                "incident_id": "INC-2025-001",
                "type": "security",
                "status": "in_progress",
                "reported_at": "2025-10-15T09:30:00Z",
                "summary": "외부 이메일로 내부 문서 전송 건",
                "severity": "medium",
                "assigned_to": "보안팀",
                "related_policies": ["정보보안정책 제3조", "개인정보처리방침 제5조"]
            }

        Note:
            - 실명/사번 등 민감 정보는 익명화되어 반환됨
            - 징계 결과 등 확정되지 않은 정보는 포함되지 않음

        TODO: 백엔드 API 스펙 확정 후 실제 필드명으로 변경
        """
        if not self.is_configured:
            logger.debug("Backend URL not configured, returning mock incident detail")
            return self._mock_incident_detail(incident_id)

        endpoint_path = BACKEND_INCIDENT_DETAIL_PATH.format(incident_id=incident_id)
        endpoint = f"{self._base_url}{endpoint_path}"

        return await self._get_request(endpoint, {})

    async def get_report_guide(
        self,
        incident_type: Optional[str] = None,
    ) -> BackendDataResponse:
        """
        신고 플로우 안내 정보를 조회합니다.

        EMPLOYEE × INCIDENT_REPORT 라우트에서 사용됩니다.

        Args:
            incident_type: 신고 유형 (security, privacy, harassment 등)

        Returns:
            BackendDataResponse: 신고 안내 정보

        Expected response data structure:
            {
                "guide_type": "security",
                "title": "보안사고 신고 안내",
                "steps": [
                    "1. 사고 발생 일시 및 장소 확인",
                    "2. 관련 증거 자료 수집 (스크린샷, 로그 등)",
                    "3. 공식 신고 채널을 통해 접수",
                    "4. 신고 접수 번호 수령 후 보관"
                ],
                "official_channels": [
                    {"name": "보안팀 직통", "contact": "security@company.com"},
                    {"name": "신고 포털", "url": "https://report.company.com"}
                ],
                "warnings": [
                    "개인정보(주민번호, 연락처 등)를 신고 내용에 포함하지 마세요.",
                    "증거 자료는 원본을 보존하고 복사본을 제출해 주세요."
                ]
            }

        TODO: 백엔드 API 스펙 확정 후 실제 필드명으로 변경
        """
        if not self.is_configured:
            logger.debug("Backend URL not configured, returning mock report guide")
            return self._mock_report_guide(incident_type)

        endpoint = f"{self._base_url}{BACKEND_REPORT_GUIDE_PATH}"
        params: Dict[str, Any] = {}
        if incident_type:
            params["type"] = incident_type

        return await self._get_request(endpoint, params)

    # =========================================================================
    # HTTP 요청 헬퍼
    # =========================================================================

    async def _get_request(
        self,
        endpoint: str,
        params: Dict[str, Any],
        raise_on_error: bool = False,
    ) -> BackendDataResponse:
        """
        GET 요청을 수행합니다.

        Phase 12: 재시도 로직 및 에러 래핑 추가.

        Args:
            endpoint: 요청 URL
            params: 쿼리 파라미터
            raise_on_error: True이면 에러 시 UpstreamServiceError 발생

        Returns:
            BackendDataResponse
        """
        start_time = time.perf_counter()

        try:
            client = get_async_http_client()

            # Phase 12: 재시도 로직 적용
            response = await retry_async_operation(
                client.get,
                endpoint,
                params=params if params else None,
                headers=self._get_auth_headers(),
                timeout=self._timeout,
                config=BACKEND_RETRY_CONFIG,
                operation_name="backend_data_request",
            )

            # latency 기록
            self._last_latency_ms = int((time.perf_counter() - start_time) * 1000)

            if response.status_code == 200:
                try:
                    data = response.json()
                    return BackendDataResponse(success=True, data=data)
                except Exception:
                    error_msg = "Invalid JSON response from backend"
                    if raise_on_error:
                        raise UpstreamServiceError(
                            service=ServiceType.BACKEND,
                            error_type=ErrorType.UPSTREAM_ERROR,
                            message=error_msg,
                        )
                    return BackendDataResponse(success=False, error_message=error_msg)
            else:
                logger.warning(
                    f"Backend data request failed: status={response.status_code}, "
                    f"endpoint={endpoint}"
                )
                error_msg = f"HTTP {response.status_code}"
                if raise_on_error:
                    raise UpstreamServiceError(
                        service=ServiceType.BACKEND,
                        error_type=ErrorType.UPSTREAM_ERROR,
                        message=error_msg,
                        status_code=response.status_code,
                    )
                return BackendDataResponse(success=False, error_message=error_msg)

        except UpstreamServiceError:
            # 이미 래핑된 예외는 그대로 raise
            self._last_latency_ms = int((time.perf_counter() - start_time) * 1000)
            raise

        except httpx.TimeoutException as e:
            self._last_latency_ms = int((time.perf_counter() - start_time) * 1000)
            logger.warning(f"Backend data request timeout after {self._timeout}s")
            if raise_on_error:
                raise UpstreamServiceError(
                    service=ServiceType.BACKEND,
                    error_type=ErrorType.UPSTREAM_TIMEOUT,
                    message=f"Backend timeout after {self._timeout}s",
                    is_timeout=True,
                    original_error=e,
                )
            return BackendDataResponse(
                success=False,
                error_message=f"Timeout after {self._timeout}s",
            )

        except Exception as e:
            self._last_latency_ms = int((time.perf_counter() - start_time) * 1000)
            logger.warning(f"Backend data request error: {e}")
            if raise_on_error:
                raise UpstreamServiceError(
                    service=ServiceType.BACKEND,
                    error_type=ErrorType.UPSTREAM_ERROR,
                    message=f"Backend error: {type(e).__name__}",
                    original_error=e,
                )
            return BackendDataResponse(success=False, error_message=str(e))

    def get_last_latency_ms(self) -> Optional[int]:
        """마지막 요청의 latency를 반환합니다 (Phase 12)."""
        return self._last_latency_ms

    # =========================================================================
    # Mock 응답 (백엔드 미연동 시 사용)
    # =========================================================================

    def _mock_employee_edu_status(self, user_id: str) -> BackendDataResponse:
        """테스트/개발용 mock 교육 현황 데이터."""
        return BackendDataResponse(
            success=True,
            data={
                "user_id": user_id,
                "total_required": 4,
                "completed": 3,
                "pending": 1,
                "courses": [
                    {"name": "정보보호교육", "status": "completed", "completed_at": "2025-03-15"},
                    {"name": "개인정보보호교육", "status": "completed", "completed_at": "2025-04-20"},
                    {"name": "직장 내 괴롭힘 방지", "status": "completed", "completed_at": "2025-05-10"},
                    {"name": "산업안전보건", "status": "pending", "deadline": "2025-12-31"},
                ],
                "next_deadline": "2025-12-31",
            },
        )

    def _mock_department_edu_stats(self, department_id: Optional[str]) -> BackendDataResponse:
        """테스트/개발용 mock 부서 교육 통계 데이터."""
        return BackendDataResponse(
            success=True,
            data={
                "department_id": department_id or "all",
                "department_name": "전체" if not department_id else "개발팀",
                "total_employees": 50,
                "completion_rate": 85.0,
                "by_course": [
                    {"name": "정보보호교육", "completed": 45, "pending": 5},
                    {"name": "개인정보보호교육", "completed": 42, "pending": 8},
                    {"name": "직장 내 괴롭힘 방지", "completed": 48, "pending": 2},
                    {"name": "산업안전보건", "completed": 40, "pending": 10},
                ],
                "pending_count": 15,
            },
        )

    def _mock_incident_overview(self, filters: Optional[Dict[str, Any]]) -> BackendDataResponse:
        """테스트/개발용 mock 사고 통계 데이터."""
        return BackendDataResponse(
            success=True,
            data={
                "period": "2025-Q4",
                "total_incidents": 15,
                "by_status": {
                    "open": 3,
                    "in_progress": 5,
                    "closed": 7,
                },
                "by_type": {
                    "security": 8,
                    "privacy": 5,
                    "compliance": 2,
                },
                "trend": {
                    "previous_period": 12,
                    "change_rate": 25.0,
                },
            },
        )

    def _mock_incident_detail(self, incident_id: str) -> BackendDataResponse:
        """테스트/개발용 mock 사건 상세 데이터."""
        return BackendDataResponse(
            success=True,
            data={
                "incident_id": incident_id,
                "type": "security",
                "status": "in_progress",
                "reported_at": "2025-10-15T09:30:00Z",
                "summary": "외부 이메일로 내부 문서 전송 건",
                "severity": "medium",
                "assigned_to": "보안팀",
                "related_policies": ["정보보안정책 제3조", "개인정보처리방침 제5조"],
            },
        )

    def _mock_report_guide(self, incident_type: Optional[str]) -> BackendDataResponse:
        """테스트/개발용 mock 신고 안내 데이터."""
        guide_type = incident_type or "general"
        return BackendDataResponse(
            success=True,
            data={
                "guide_type": guide_type,
                "title": f"{guide_type.upper()} 사고 신고 안내",
                "steps": [
                    "1. 사고 발생 일시 및 장소 확인",
                    "2. 관련 증거 자료 수집 (스크린샷, 로그 등)",
                    "3. 공식 신고 채널을 통해 접수",
                    "4. 신고 접수 번호 수령 후 보관",
                ],
                "official_channels": [
                    {"name": "보안팀 직통", "contact": "security@company.com"},
                    {"name": "신고 포털", "url": "https://report.company.com"},
                ],
                "warnings": [
                    "개인정보(주민번호, 연락처 등)를 신고 내용에 포함하지 마세요.",
                    "증거 자료는 원본을 보존하고 복사본을 제출해 주세요.",
                ],
            },
        )
