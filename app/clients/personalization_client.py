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

from app.clients.http_client import get_async_http_client
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
    ) -> PersonalizationFacts:
        """
        개인화 facts를 조회합니다.

        백엔드 POST /api/personalization/resolve 호출.

        Args:
            sub_intent_id: Q1-Q20 인텐트 ID
            user_id: 사용자 ID (X-User-Id 헤더로 전달)
            period: 기간 유형 (this-week|this-month|3m|this-year)

        Returns:
            PersonalizationFacts: 조회된 facts 데이터 (에러 시 error 필드 포함)
        """
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

        # 백엔드 URL 미설정 시 mock 응답
        if not self._base_url:
            logger.debug("Backend URL not configured, returning mock facts")
            return self._get_mock_facts(sub_intent_id, period)

        endpoint = f"{self._base_url}{self.RESOLVE_PATH}"

        try:
            client = get_async_http_client()

            # 요청 페이로드 생성
            request_data = PersonalizationResolveRequest(
                sub_intent_id=sub_intent_id,
                period=period,
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
                    error=PersonalizationError(
                        type=PersonalizationErrorType.NOT_FOUND.value,
                        message="Data not found for the specified period",
                    ),
                )
            else:
                logger.warning(
                    f"Personalization resolve failed: status={response.status_code}, "
                    f"body={response.text[:200]}"
                )
                return PersonalizationFacts(
                    sub_intent_id=sub_intent_id,
                    error=PersonalizationError(
                        type=PersonalizationErrorType.TIMEOUT.value,
                        message=f"HTTP {response.status_code}",
                    ),
                )

        except Exception as e:
            logger.warning(f"Personalization resolve error: {e}")
            return PersonalizationFacts(
                sub_intent_id=sub_intent_id,
                error=PersonalizationError(
                    type=PersonalizationErrorType.TIMEOUT.value,
                    message=str(e),
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
            "Q5": {  # 내 평균 vs 부서/전사 평균
                "metrics": {
                    "my_average": 85.5,
                    "dept_average": 82.3,
                    "company_average": 80.1,
                },
                "extra": {
                    "dept_name": "개발팀",
                },
            },
            "Q6": {  # 가장 많이 틀린 보안 토픽 TOP3
                "items": [
                    {"rank": 1, "topic": "피싱 메일 식별", "wrong_rate": 35.2},
                    {"rank": 2, "topic": "비밀번호 정책", "wrong_rate": 28.7},
                    {"rank": 3, "topic": "개인정보 처리", "wrong_rate": 22.1},
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
            "Q11": {  # 남은 연차 일수
                "metrics": {
                    "total_days": 15,
                    "used_days": 8,
                    "remaining_days": 7,
                },
            },
            "Q14": {  # 복지/식대 포인트 잔액
                "metrics": {
                    "welfare_points": 150000,
                    "meal_allowance": 280000,
                },
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
        }

        return mock_responses.get(sub_intent_id, {"metrics": {}, "items": []})
