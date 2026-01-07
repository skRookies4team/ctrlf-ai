"""
개인화 API 클라이언트 모듈 (Personalization API Client Module)

ctrlf-back (Spring 백엔드)의 개인화 API와 통신하는 HTTP 클라이언트입니다.
facts 조회 API 호출을 담당합니다.

주요 기능:
- resolve_facts: 개인화 facts 데이터 조회 (POST /api/personalization/resolve)

사용 방법:
    from app.clients.personalization_client import PersonalizationClient

    client = PersonalizationClient()
    facts = await client.resolve_facts("Q11", user_id="emp123", period="this-year")
"""

from typing import Optional

import httpx

from app.clients.http_client import get_async_http_client
from app.core.backend_context import check_backend_allowed
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.personalization import (
    DEFAULT_PERIOD_FOR_INTENT,
    PersonalizationError,
    PersonalizationErrorType,
    PersonalizationFacts,
    PersonalizationResolveRequest,
    PeriodType,
    PRIORITY_SUB_INTENTS,
)

logger = get_logger(__name__)
settings = get_settings()


class PersonalizationClient:
    """
    개인화 API 클라이언트.

    ctrlf-back (Spring 백엔드)의 개인화 API와 통신합니다.
    facts 조회 기능을 제공합니다.

    Attributes:
        _base_url: 백엔드 서비스 base URL
        _api_token: API 인증 토큰 (선택사항)
        _timeout: HTTP 요청 타임아웃 (초)

    Usage:
        client = PersonalizationClient()
        facts = await client.resolve_facts("Q11", user_id="emp123")
    """

    # API 경로 상수
    RESOLVE_PATH = "/api/personalization/resolve"

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        """
        PersonalizationClient 초기화.

        Args:
            base_url: 백엔드 서비스 URL. None이면 설정에서 가져옴.
            api_token: API 인증 토큰. None이면 설정에서 가져옴.
            timeout: HTTP 요청 타임아웃 (초). 기본 10초.
        """
        self._base_url = base_url or settings.backend_base_url
        self._api_token = api_token if api_token is not None else settings.BACKEND_API_TOKEN
        self._timeout = timeout

    @property
    def is_configured(self) -> bool:
        """백엔드 URL이 설정되었는지 확인."""
        return self._base_url is not None

    def _get_auth_headers(self) -> dict[str, str]:
        """
        인증 헤더를 반환합니다.

        Returns:
            dict[str, str]: 인증 헤더 딕셔너리
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    async def resolve_facts(
        self,
        sub_intent_id: str,
        user_id: str,
        period: Optional[str] = None,
        target_dept_id: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> PersonalizationFacts:
        """
        개인화 facts를 조회합니다.

        백엔드 POST /api/personalization/resolve 호출.

        Args:
            sub_intent_id: Q1-Q20 인텐트 ID
            user_id: 사용자 ID (X-User-Id 헤더로 전달)
            period: 기간 유형 (this-week|this-month|3m|this-year)
            target_dept_id: 부서 비교 대상 ID (향후 사용 예정)
            topic: 교육 토픽 (Q2, Q7, Q8, Q18, Q19에서 사용)

        Returns:
            PersonalizationFacts: 조회된 facts 데이터 (에러 시 error 필드 포함)

        Raises:
            BackendBlockedError: Backend API가 차단된 경우 (금지질문)
        """
        # Step 3: 2차 가드 - Backend API 차단 여부 확인
        check_backend_allowed("PersonalizationClient.resolve_facts")

        # 기본 period 설정 (미지정 시)
        if period is None:
            period = DEFAULT_PERIOD_FOR_INTENT.get(sub_intent_id, PeriodType.THIS_YEAR).value

        # 우선순위 인텐트가 아닌 경우 NOT_IMPLEMENTED 반환
        if sub_intent_id not in PRIORITY_SUB_INTENTS:
            logger.info(f"Sub-intent {sub_intent_id} not yet implemented")
            return PersonalizationFacts(
                sub_intent_id=sub_intent_id,
                error=PersonalizationError(
                    type=PersonalizationErrorType.NOT_IMPLEMENTED.value,
                    message=f"Sub-intent {sub_intent_id} is not yet implemented",
                ),
            )

        # =========================================================================
        # PERSONALIZATION_MODE 분기
        # - mock: 무조건 mock 데이터 반환
        # - real: 실 백엔드만, 실패 시 에러 반환 (base_url 없으면 CONFIG_ERROR)
        # - auto: 실 백엔드 시도, 네트워크 실패 시 mock fallback
        # =========================================================================
        mode = settings.PERSONALIZATION_MODE

        # mock 모드: 무조건 mock 반환
        if mode == "mock":
            logger.debug(f"PERSONALIZATION_MODE=mock, returning mock facts for {sub_intent_id}")
            return self._get_mock_facts(sub_intent_id, period)

        # 백엔드 URL 미설정 시: 모드에 따라 분기
        if not self._base_url:
            if mode == "auto":
                # auto 모드: URL 없으면 mock fallback
                logger.debug("Backend URL not configured (auto mode), returning mock facts")
                return self._get_mock_facts(sub_intent_id, period)
            # real 모드: URL 없으면 에러 반환
            logger.warning("Backend URL not configured (real mode)")
            return PersonalizationFacts(
                sub_intent_id=sub_intent_id,
                items=[],
                metrics={},
                error=PersonalizationError(
                    type=PersonalizationErrorType.CONFIG_ERROR.value,
                    message="BACKEND_BASE_URL is not set (PERSONALIZATION_MODE=real)",
                ),
            )

        endpoint = f"{self._base_url}{self.RESOLVE_PATH}"

        try:
            client = get_async_http_client()

            # 요청 페이로드 생성
            request_data = PersonalizationResolveRequest(
                sub_intent_id=sub_intent_id,
                period=period,
                target_dept_id=target_dept_id,
                topic=topic,
            )

            # 헤더에 X-User-Id 추가
            headers = self._get_auth_headers()
            headers["X-User-Id"] = user_id

            response = await client.post(
                endpoint,
                json=request_data.model_dump(exclude_none=True),
                headers=headers,
                timeout=self._timeout,
            )

            if response.status_code == 200:
                data = response.json()
                return PersonalizationFacts(**data)
            elif response.status_code == 404:
                return PersonalizationFacts(
                    sub_intent_id=sub_intent_id,
                    items=[],
                    metrics={},
                    error=PersonalizationError(
                        type=PersonalizationErrorType.NOT_FOUND.value,
                        message="Data not found for the specified period",
                    ),
                )
            else:
                logger.warning(
                    f"Personalization resolve failed: status={response.status_code}, "
                    f"body_len={len(response.text)}"
                )
                return PersonalizationFacts(
                    sub_intent_id=sub_intent_id,
                    items=[],
                    metrics={},
                    error=PersonalizationError(
                        type=PersonalizationErrorType.HTTP_ERROR.value,
                        message=f"HTTP {response.status_code}",
                    ),
                )

        # 타임아웃 계열 예외: auto fallback 대상
        # httpx.TimeoutException은 ConnectTimeout, ReadTimeout, WriteTimeout, PoolTimeout의 부모 클래스
        except httpx.TimeoutException as e:
            timeout_type = type(e).__name__  # 로그에서 구체적 타입 구분
            if mode == "auto":
                logger.warning(f"Personalization timeout ({timeout_type}, auto fallback to mock): {e}")
                return self._get_mock_facts(sub_intent_id, period)

            logger.warning(f"Personalization timeout ({timeout_type}): {e}")
            return PersonalizationFacts(
                sub_intent_id=sub_intent_id,
                items=[],
                metrics={},
                error=PersonalizationError(
                    type=PersonalizationErrorType.TIMEOUT.value,
                    message=f"{timeout_type}: {e}",
                ),
            )

        # 연결 에러 계열 예외: auto fallback 대상
        except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
            error_type = type(e).__name__  # 로그에서 구체적 타입 구분
            if mode == "auto":
                logger.warning(f"Personalization network error ({error_type}, auto fallback to mock): {e}")
                return self._get_mock_facts(sub_intent_id, period)

            logger.warning(f"Personalization network error ({error_type}): {e}")
            return PersonalizationFacts(
                sub_intent_id=sub_intent_id,
                items=[],
                metrics={},
                error=PersonalizationError(
                    type=PersonalizationErrorType.NETWORK_ERROR.value,
                    message=f"{error_type}: {e}",
                ),
            )

        # 기타 예외(JSON 파싱 실패, 스키마 불일치 등)는 항상 에러 반환 (버그 조기 탐지)
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Personalization resolve unexpected error ({error_type}): {e}")
            return PersonalizationFacts(
                sub_intent_id=sub_intent_id,
                items=[],
                metrics={},
                error=PersonalizationError(
                    type=PersonalizationErrorType.UNEXPECTED_ERROR.value,
                    message=f"{error_type}: {e}",
                ),
            )

    def _get_mock_facts(
        self,
        sub_intent_id: str,
        period: Optional[str],
    ) -> PersonalizationFacts:
        """개발/테스트용 mock facts 반환."""
        from datetime import datetime, timedelta

        now = datetime.now()

        # 기간 계산
        if period == "this-week":
            period_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
            period_end = now.strftime("%Y-%m-%d")
        elif period == "this-month":
            period_start = now.replace(day=1).strftime("%Y-%m-%d")
            period_end = now.strftime("%Y-%m-%d")
        elif period == "3m":
            period_start = (now - timedelta(days=90)).strftime("%Y-%m-%d")
            period_end = now.strftime("%Y-%m-%d")
        else:  # this-year
            period_start = now.replace(month=1, day=1).strftime("%Y-%m-%d")
            period_end = now.strftime("%Y-%m-%d")

        # 인텐트별 mock 데이터
        mock_data = self._get_mock_data_for_intent(sub_intent_id)

        return PersonalizationFacts(
            sub_intent_id=sub_intent_id,
            period_start=period_start,
            period_end=period_end,
            updated_at=now.isoformat(),
            metrics=mock_data.get("metrics", {}),
            items=mock_data.get("items", []),
            extra=mock_data.get("extra", {}),
        )

    def _get_mock_data_for_intent(
        self,
        sub_intent_id: str,
    ) -> dict:
        """인텐트별 mock 데이터 반환."""
        mock_responses = {
            "Q1": {  # 미이수 필수 교육 조회
                "metrics": {"total_required": 5, "completed": 3, "remaining": 2},
                "items": [
                    {"education_id": "EDU001", "title": "개인정보보호 교육", "deadline": "2025-01-31", "status": "미이수"},
                    {"education_id": "EDU002", "title": "정보보안 교육", "deadline": "2025-02-15", "status": "미이수"},
                ],
            },
            "Q3": {  # 이번 달 데드라인 필수 교육
                "metrics": {"deadline_count": 2},
                "items": [
                    {"education_id": "EDU001", "title": "개인정보보호 교육", "deadline": "2025-01-31", "days_left": 13},
                    {"education_id": "EDU003", "title": "직장 내 괴롭힘 예방교육", "deadline": "2025-01-25", "days_left": 7},
                ],
            },
            "Q4": {  # 특정 교육 진도율/시청률 조회 (이어보기)
                "metrics": {"progress_percent": 65, "total_watch_seconds": 1170},
                "items": [
                    {
                        "education_id": "EDU001",
                        "video_id": "VID001",
                        "education_title": "개인정보보호 교육",
                        "video_title": "개인정보보호 기본",
                        "resumePosition": 1170,  # 백엔드 필드명 (초 단위)
                        "progress_percent": 65,
                        "duration": 1800,
                    },
                ],
            },
            "Q9": {  # 이번 주 교육/퀴즈 할 일
                "metrics": {"todo_count": 3},
                "items": [
                    {"type": "education", "title": "정보보안 교육", "deadline": "2025-01-20"},
                    {"type": "quiz", "title": "보안 퀴즈", "deadline": "2025-01-19"},
                    {"type": "education", "title": "개인정보보호 교육", "deadline": "2025-01-21"},
                ],
            },
            "Q10": {  # 내 근태 현황 조회
                "metrics": {
                    "work_days": 22,         # 이번 달 근무일수
                    "actual_work_days": 20,  # 실제 출근일
                    "late_count": 1,         # 지각 횟수
                    "early_leave_count": 0,  # 조퇴 횟수
                    "absent_count": 0,       # 결근 횟수
                    "remote_days": 4,        # 재택근무 일수
                    "overtime_hours": 12.5,  # 초과근무 시간
                },
                "items": [
                    {
                        "date": "2025-01-06",
                        "day_of_week": "월",
                        "check_in": "09:00",
                        "check_out": "18:30",
                        "work_hours": 8.5,
                        "status": "정상",
                        "work_type": "출근",
                    },
                    {
                        "date": "2025-01-03",
                        "day_of_week": "금",
                        "check_in": "09:15",
                        "check_out": "18:00",
                        "work_hours": 7.75,
                        "status": "지각",
                        "work_type": "출근",
                    },
                    {
                        "date": "2025-01-02",
                        "day_of_week": "목",
                        "check_in": "09:00",
                        "check_out": "19:30",
                        "work_hours": 9.5,
                        "status": "정상",
                        "work_type": "재택",
                    },
                    {
                        "date": "2024-12-30",
                        "day_of_week": "월",
                        "check_in": "08:55",
                        "check_out": "18:00",
                        "work_hours": 8.0,
                        "status": "정상",
                        "work_type": "출근",
                    },
                    {
                        "date": "2024-12-27",
                        "day_of_week": "금",
                        "check_in": "09:00",
                        "check_out": "20:00",
                        "work_hours": 10.0,
                        "status": "정상",
                        "work_type": "재택",
                    },
                ],
            },
            "Q11": {  # 남은 연차 일수
                "metrics": {
                    "total_days": 15,
                    "used_days": 8,
                    "remaining_days": 7,
                },
            },
            "Q12": {  # 연차 사용 이력 조회
                "metrics": {
                    "total_days": 15,
                    "used_days": 8,
                    "remaining_days": 7,
                    "usage_count": 5,
                },
                "items": [
                    {
                        "leave_id": "LV001",
                        "leave_type": "연차",
                        "start_date": "2025-01-02",
                        "end_date": "2025-01-03",
                        "days": 2,
                        "reason": "개인 사유",
                        "status": "승인완료",
                    },
                    {
                        "leave_id": "LV002",
                        "leave_type": "연차",
                        "start_date": "2024-12-24",
                        "end_date": "2024-12-25",
                        "days": 2,
                        "reason": "연말 휴가",
                        "status": "승인완료",
                    },
                    {
                        "leave_id": "LV003",
                        "leave_type": "반차",
                        "start_date": "2024-11-15",
                        "end_date": "2024-11-15",
                        "days": 0.5,
                        "reason": "병원 방문",
                        "status": "승인완료",
                    },
                    {
                        "leave_id": "LV004",
                        "leave_type": "연차",
                        "start_date": "2024-10-01",
                        "end_date": "2024-10-02",
                        "days": 2,
                        "reason": "가족 행사",
                        "status": "승인완료",
                    },
                    {
                        "leave_id": "LV005",
                        "leave_type": "반차",
                        "start_date": "2024-09-10",
                        "end_date": "2024-09-10",
                        "days": 0.5,
                        "reason": "개인 사유",
                        "status": "승인완료",
                    },
                ],
            },
            "Q14": {  # 복지/식대 포인트 잔액
                "metrics": {
                    "welfare_points": 150000,
                    "meal_allowance": 280000,
                    "total_granted": 500000,
                    "total_used": 350000,
                },
            },
            "Q15": {  # 복지 포인트 사용 내역 조회
                "metrics": {
                    "total_granted": 500000,
                    "total_used": 350000,
                    "remaining": 150000,
                    "usage_count": 6,
                },
                "items": [
                    {
                        "usage_id": "WF001",
                        "category": "건강/의료",
                        "merchant": "강남세브란스병원",
                        "amount": 85000,
                        "date": "2025-01-03",
                        "description": "건강검진 비용",
                    },
                    {
                        "usage_id": "WF002",
                        "category": "자기계발",
                        "merchant": "교보문고",
                        "amount": 45000,
                        "date": "2024-12-20",
                        "description": "도서 구입",
                    },
                    {
                        "usage_id": "WF003",
                        "category": "여가/문화",
                        "merchant": "CGV",
                        "amount": 28000,
                        "date": "2024-12-15",
                        "description": "영화 관람",
                    },
                    {
                        "usage_id": "WF004",
                        "category": "건강/의료",
                        "merchant": "올리브영",
                        "amount": 52000,
                        "date": "2024-11-28",
                        "description": "건강용품 구입",
                    },
                    {
                        "usage_id": "WF005",
                        "category": "자기계발",
                        "merchant": "클래스101",
                        "amount": 99000,
                        "date": "2024-10-15",
                        "description": "온라인 강의 수강",
                    },
                    {
                        "usage_id": "WF006",
                        "category": "여가/문화",
                        "merchant": "스타벅스",
                        "amount": 41000,
                        "date": "2024-09-20",
                        "description": "카페 이용",
                    },
                ],
            },
            "Q20": {  # 올해 HR 할 일 (미완료)
                "metrics": {"todo_count": 4},
                "items": [
                    {"type": "education", "title": "필수 교육 2건", "status": "미완료"},
                    {"type": "document", "title": "연말정산 서류 제출", "deadline": "2025-01-31"},
                    {"type": "survey", "title": "직원 만족도 조사", "deadline": "2025-02-28"},
                    {"type": "review", "title": "상반기 성과 평가", "deadline": "2025-06-30"},
                ],
            },
            "Q2": {  # 수료현황/진도
                "metrics": {
                    "total_educations": 10,
                    "completed_educations": 7,
                    "in_progress_educations": 2,
                    "not_started_educations": 1,
                    "completion_rate": 70.0,
                },
                "items": [
                    {"education_id": "EDU001", "title": "개인정보보호 교육", "status": "완료", "progress_percent": 100},
                    {"education_id": "EDU002", "title": "정보보안 교육", "status": "진행중", "progress_percent": 45},
                    {"education_id": "EDU003", "title": "직장 내 괴롭힘 예방", "status": "미시작", "progress_percent": 0},
                ],
            },
            "Q5": {  # 퀴즈 평균 점수
                "metrics": {
                    "average_score": 82.5,
                    "total_quizzes": 8,
                    "passed_quizzes": 7,
                    "failed_quizzes": 1,
                },
                "items": [
                    {"quiz_id": "QUIZ001", "title": "개인정보보호 퀴즈", "score": 90, "pass_score": 70, "status": "합격"},
                    {"quiz_id": "QUIZ002", "title": "정보보안 퀴즈", "score": 85, "pass_score": 70, "status": "합격"},
                    {"quiz_id": "QUIZ003", "title": "직장내괴롭힘 퀴즈", "score": 60, "pass_score": 70, "status": "불합격"},
                ],
            },
            "Q6": {  # 낮은/높은 점수 과목
                "metrics": {
                    "lowest_score": 60,
                    "highest_score": 95,
                },
                "items": [
                    {"quiz_id": "QUIZ003", "title": "직장내괴롭힘 퀴즈", "score": 60, "rank": "lowest"},
                    {"quiz_id": "QUIZ005", "title": "성희롱예방 퀴즈", "score": 95, "rank": "highest"},
                ],
            },
            "Q7": {  # 미완료/재응시 퀴즈 조회
                "metrics": {"pending_count": 2, "retry_count": 1},
                "items": [
                    {"quiz_id": "QUIZ006", "title": "개인정보보호 심화 퀴즈", "status": "미응시", "deadline": "2025-01-31"},
                    {"quiz_id": "QUIZ003", "title": "직장내괴롭힘 퀴즈", "status": "재응시 필요", "last_score": 60, "pass_score": 70},
                ],
            },
            "Q8": {  # 특정 토픽 교육 시청 완료 여부 (영상만)
                "metrics": {
                    "topic_label": "개인정보보호",
                    "video_count": 3,
                    "watched_count": 2,
                    "is_all_watched": False,
                },
                "items": [
                    {"video_id": "VID001", "title": "개인정보보호 기본", "watched": True, "watch_percent": 100},
                    {"video_id": "VID002", "title": "개인정보보호 심화", "watched": True, "watch_percent": 100},
                    {"video_id": "VID003", "title": "개인정보보호 사례", "watched": False, "watch_percent": 35},
                ],
            },
            "Q18": {  # 특정 토픽 전체 완료 여부 (영상 + 퀴즈)
                "metrics": {
                    "topic_label": "개인정보보호",
                    "is_completed": False,
                    "video_completed": True,
                    "quiz_completed": False,
                    "video_count": 3,
                    "quiz_count": 1,
                },
                "items": [
                    {"type": "video", "title": "개인정보보호 교육 영상", "status": "완료"},
                    {"type": "quiz", "title": "개인정보보호 퀴즈", "status": "미완료", "score": None, "pass_score": 70},
                ],
            },
            "Q19": {  # 특정 토픽 교육 마감일 조회
                "metrics": {
                    "topic_label": "개인정보보호",
                    "deadline": "2025-01-31",
                    "days_left": 13,
                },
                "items": [
                    {"education_id": "EDU001", "title": "개인정보보호 교육", "deadline": "2025-01-31", "status": "진행중"},
                ],
            },
            "Q13": {  # 급여 명세서 요약
                "metrics": {
                    "pay_month": "2025-01",
                    "base_salary": 4500000,          # 기본급
                    "overtime_pay": 350000,          # 연장근로수당
                    "bonus": 0,                      # 상여금
                    "meal_allowance": 100000,        # 식대
                    "transport_allowance": 100000,   # 교통비
                    "total_earnings": 5050000,       # 총 지급액
                    "income_tax": 215000,            # 소득세
                    "local_tax": 21500,              # 지방소득세
                    "national_pension": 202500,      # 국민연금
                    "health_insurance": 177750,      # 건강보험
                    "long_term_care": 22800,         # 장기요양보험
                    "employment_insurance": 45450,   # 고용보험
                    "total_deductions": 685000,      # 총 공제액
                    "net_pay": 4365000,              # 실수령액
                },
                "items": [
                    {"category": "지급", "item": "기본급", "amount": 4500000},
                    {"category": "지급", "item": "연장근로수당", "amount": 350000},
                    {"category": "지급", "item": "식대", "amount": 100000},
                    {"category": "지급", "item": "교통비", "amount": 100000},
                    {"category": "공제", "item": "소득세", "amount": -215000},
                    {"category": "공제", "item": "지방소득세", "amount": -21500},
                    {"category": "공제", "item": "국민연금", "amount": -202500},
                    {"category": "공제", "item": "건강보험", "amount": -177750},
                    {"category": "공제", "item": "장기요양보험", "amount": -22800},
                    {"category": "공제", "item": "고용보험", "amount": -45450},
                ],
            },
            "Q16": {  # 내 인사 정보 조회
                "metrics": {
                    "employee_id": "EMP20210315",
                    "name": "홍길동",
                    "department": "개발팀",
                    "position": "선임연구원",
                    "job_title": "백엔드 개발자",
                    "hire_date": "2021-03-15",
                    "years_of_service": 3,
                    "months_of_service": 10,
                    "email": "hong.gildong@company.com",
                    "phone": "010-1234-5678",
                    "office_phone": "02-1234-5678",
                },
                "items": [
                    {"label": "사원번호", "value": "EMP20210315"},
                    {"label": "이름", "value": "홍길동"},
                    {"label": "부서", "value": "개발팀"},
                    {"label": "직급", "value": "선임연구원"},
                    {"label": "직책", "value": "백엔드 개발자"},
                    {"label": "입사일", "value": "2021-03-15"},
                    {"label": "근속연수", "value": "3년 10개월"},
                    {"label": "이메일", "value": "hong.gildong@company.com"},
                    {"label": "휴대폰", "value": "010-1234-5678"},
                    {"label": "사내전화", "value": "02-1234-5678"},
                ],
            },
            "Q17": {  # 내 팀/부서 정보 조회
                "metrics": {
                    "department_name": "개발팀",
                    "department_code": "DEV001",
                    "team_lead": "김팀장",
                    "team_lead_position": "팀장",
                    "total_members": 8,
                    "full_time": 7,
                    "contract": 1,
                    "parent_department": "기술본부",
                },
                "items": [
                    {"employee_id": "EMP001", "name": "김팀장", "position": "팀장", "job_title": "개발팀장", "is_leader": True},
                    {"employee_id": "EMP002", "name": "이수석", "position": "수석연구원", "job_title": "테크리드", "is_leader": False},
                    {"employee_id": "EMP003", "name": "박선임", "position": "선임연구원", "job_title": "프론트엔드 개발자", "is_leader": False},
                    {"employee_id": "EMP004", "name": "홍길동", "position": "선임연구원", "job_title": "백엔드 개발자", "is_leader": False},
                    {"employee_id": "EMP005", "name": "최주임", "position": "주임연구원", "job_title": "풀스택 개발자", "is_leader": False},
                    {"employee_id": "EMP006", "name": "정사원", "position": "연구원", "job_title": "주니어 개발자", "is_leader": False},
                    {"employee_id": "EMP007", "name": "강사원", "position": "연구원", "job_title": "주니어 개발자", "is_leader": False},
                    {"employee_id": "EMP008", "name": "윤인턴", "position": "인턴", "job_title": "인턴 개발자", "is_leader": False},
                ],
            },
        }

        return mock_responses.get(sub_intent_id, {"metrics": {}, "items": []})
