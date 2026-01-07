"""
Personalization Models (개인화 모델)

백엔드 개인화 API 연동을 위한 Pydantic 모델을 정의합니다.
prompt.txt 스펙에 따른 개인화 요청/응답 스키마입니다.

주요 모델:
- PeriodType: 기간 유형 (this-week, this-month, 3m, this-year)
- PersonalizationSubIntentId: Q1-Q20 개인화 인텐트 ID
- PersonalizationResolveRequest: 개인화 조회 요청
- PersonalizationResolveResponse: 개인화 조회 응답 (facts)
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Period Type Enum (기간 유형)
# =============================================================================


class PeriodType(str, Enum):
    """기간 유형 (4개 고정).

    prompt.txt에 정의된 기간 유형입니다.
    기간을 확정 못 하면 각 인텐트 기본 period를 사용합니다.

    Attributes:
        THIS_WEEK: 이번 주
        THIS_MONTH: 이번 달
        THREE_MONTHS: 3개월 (90일)
        THIS_YEAR: 올해
    """

    THIS_WEEK = "this-week"
    THIS_MONTH = "this-month"
    THREE_MONTHS = "3m"
    THIS_YEAR = "this-year"


# =============================================================================
# Personalization Sub-Intent ID (Q1-Q20)
# =============================================================================


class PersonalizationSubIntentId(str, Enum):
    """개인화 세부 의도 식별자 (Q1-Q20).

    prompt.txt에 정의된 개인화 스코프입니다.
    데모 "완전 구현" 우선순위 5개: Q1, Q3, Q9, Q11, Q14

    Attributes:
        Q1: 미이수 필수 교육 조회
        Q2: 내 교육 수료 현황 조회
        Q3: 이번 달 데드라인 필수 교육
        Q4: 특정 교육 진도율/시청률 조회
        Q7: 특정 교육 퀴즈 결과 조회
        Q8: 내 퀴즈 점수 이력 조회
        Q9: 이번 주 교육/퀴즈 할 일 1줄 (통합)
        Q10: 내 근태 현황 조회
        Q11: 남은 연차 일수
        Q12: 연차 사용 이력 조회
        Q13: 급여 명세서 요약
        Q14: 복지/식대 포인트 잔액
        Q15: 복지 포인트 사용 내역
        Q16: 내 인사 정보 조회
        Q17: 내 팀/부서 정보 조회
        Q18: 보안 교육 이수 현황
        Q19: 필수 교육 전체 요약
        Q20: 올해 HR 할 일 (미완료)
    """

    Q1 = "Q1"   # 미이수 필수 교육 조회
    Q2 = "Q2"   # 내 교육 수료 현황 조회
    Q3 = "Q3"   # 이번 달 데드라인 필수 교육
    Q4 = "Q4"   # 특정 교육 진도율/시청률 조회
    Q5 = "Q5"   # 내 평균 vs 부서/전사 평균
    Q6 = "Q6"   # 가장 낮은/높은 점수 교육 TOP3
    Q7 = "Q7"   # 특정 교육 퀴즈 결과 조회
    Q8 = "Q8"   # 내 퀴즈 점수 이력 조회
    Q9 = "Q9"   # 이번 주 교육/퀴즈 할 일 1줄
    Q10 = "Q10"  # 내 근태 현황 조회
    Q11 = "Q11"  # 남은 연차 일수
    Q12 = "Q12"  # 연차 사용 이력 조회
    Q13 = "Q13"  # 급여 명세서 요약
    Q14 = "Q14"  # 복지/식대 포인트 잔액
    Q15 = "Q15"  # 복지 포인트 사용 내역
    Q16 = "Q16"  # 내 인사 정보 조회
    Q17 = "Q17"  # 내 팀/부서 정보 조회
    Q18 = "Q18"  # 보안 교육 이수 현황
    Q19 = "Q19"  # 필수 교육 전체 요약
    Q20 = "Q20"  # 올해 HR 할 일 (미완료)


# 데모 완전 구현 대상 (19개) - Q13, Q16, Q17 추가 (급여/인사정보/팀정보)
PRIORITY_SUB_INTENTS = frozenset([
    PersonalizationSubIntentId.Q1.value,
    PersonalizationSubIntentId.Q2.value,   # 특정 토픽 교육 이수 여부 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q3.value,
    PersonalizationSubIntentId.Q4.value,   # 교육 이어보기 (EDU_RESUME_CHECK)
    PersonalizationSubIntentId.Q5.value,   # 내 평균 vs 부서/전사 평균 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q6.value,   # 가장 낮은/높은 점수 교육 TOP3 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q7.value,   # 특정 토픽 퀴즈 점수 조회 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q8.value,   # 특정 토픽 교육 시청 완료 여부 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q9.value,
    PersonalizationSubIntentId.Q10.value,  # 내 근태 현황 조회 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q11.value,
    PersonalizationSubIntentId.Q12.value,  # 연차 사용 이력 조회 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q13.value,  # 급여 명세서 요약 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q14.value,
    PersonalizationSubIntentId.Q15.value,  # 복지 포인트 사용 내역 조회 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q16.value,  # 내 인사 정보 조회 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q17.value,  # 내 팀/부서 정보 조회 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q18.value,  # 보안교육(특정 토픽) 완료 여부 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q19.value,  # 특정 토픽 교육 마감일 조회 (백엔드 DB 연동)
    PersonalizationSubIntentId.Q20.value,  # 올해 HR 할 일 (백엔드 DB 연동)
])


# Q별 기본 period 매핑 (기간 미지정 시 사용)
DEFAULT_PERIOD_FOR_INTENT: Dict[str, PeriodType] = {
    "Q2": PeriodType.THIS_YEAR,   # 특정 토픽 교육 이수 여부
    "Q3": PeriodType.THIS_MONTH,
    "Q5": PeriodType.THIS_YEAR,   # 내 평균 vs 부서/전사 평균
    "Q6": PeriodType.THIS_YEAR,   # 가장 낮은/높은 점수 교육 TOP3
    "Q7": PeriodType.THIS_YEAR,   # 특정 토픽 퀴즈 점수 조회
    "Q8": PeriodType.THIS_YEAR,   # 특정 토픽 교육 시청 완료 여부
    "Q9": PeriodType.THIS_WEEK,
    "Q10": PeriodType.THIS_MONTH,  # 내 근태 현황 조회
    "Q11": PeriodType.THIS_YEAR,
    "Q12": PeriodType.THIS_YEAR,  # 연차 사용 이력 조회
    "Q13": PeriodType.THIS_MONTH,  # 급여 명세서 요약 (이번 달 기본)
    "Q14": PeriodType.THIS_YEAR,  # 기간 없음이지만 기본값
    "Q15": PeriodType.THIS_YEAR,  # 복지 포인트 사용 내역 조회
    "Q16": PeriodType.THIS_YEAR,  # 내 인사 정보 조회 (기간 무관)
    "Q17": PeriodType.THIS_YEAR,  # 내 팀/부서 정보 조회 (기간 무관)
    "Q18": PeriodType.THIS_YEAR,  # 보안교육(특정 토픽) 완료 여부
    "Q19": PeriodType.THIS_YEAR,  # 특정 토픽 교육 마감일 조회
    "Q20": PeriodType.THIS_YEAR,
}

# 토픽 기반 인텐트 목록 (topic 파라미터 필요)
TOPIC_BASED_INTENTS = frozenset([
    PersonalizationSubIntentId.Q2.value,   # 특정 토픽 교육 이수 여부
    PersonalizationSubIntentId.Q7.value,   # 특정 토픽 퀴즈 점수 조회
    PersonalizationSubIntentId.Q8.value,   # 특정 토픽 교육 시청 완료 여부
    PersonalizationSubIntentId.Q18.value,  # 보안교육(특정 토픽) 완료 여부
    PersonalizationSubIntentId.Q19.value,  # 특정 토픽 교육 마감일 조회
])

# 교육 토픽 매핑 (한글 -> 영문 코드)
EDUCATION_TOPIC_MAPPING: Dict[str, str] = {
    "직장내괴롭힘": "WORKPLACE_BULLYING",
    "직장 내 괴롭힘": "WORKPLACE_BULLYING",
    "괴롭힘": "WORKPLACE_BULLYING",
    "성희롱예방": "SEXUAL_HARASSMENT_PREVENTION",
    "성희롱 예방": "SEXUAL_HARASSMENT_PREVENTION",
    "성희롱": "SEXUAL_HARASSMENT_PREVENTION",
    "개인정보보호": "PERSONAL_INFO_PROTECTION",
    "개인정보 보호": "PERSONAL_INFO_PROTECTION",
    "개인정보": "PERSONAL_INFO_PROTECTION",
    "정보보안": "PERSONAL_INFO_PROTECTION",
    "보안교육": "PERSONAL_INFO_PROTECTION",
    "장애인인식개선": "DISABILITY_AWARENESS",
    "장애인 인식 개선": "DISABILITY_AWARENESS",
    "장애인": "DISABILITY_AWARENESS",
    "직무교육": "JOB_DUTY",
    "직무 교육": "JOB_DUTY",
    "직무": "JOB_DUTY",
}


# =============================================================================
# Personalization Error Types
# =============================================================================


class PersonalizationErrorType(str, Enum):
    """개인화 에러 유형.

    백엔드 응답의 error.type 값입니다.

    Attributes:
        NOT_FOUND: 해당 기간에 조회할 데이터가 없음
        TIMEOUT: 조회 지연 (ConnectTimeout, ReadTimeout, WriteTimeout, PoolTimeout)
        NETWORK_ERROR: 네트워크 연결 실패 (ConnectError, RemoteProtocolError)
        HTTP_ERROR: HTTP 응답 에러 (2xx/404 외 상태코드)
        CONFIG_ERROR: 설정 오류 (예: BACKEND_BASE_URL 미설정)
        PARTIAL: 일부 정보만 가져올 수 있음
        NOT_IMPLEMENTED: 아직 구현되지 않은 인텐트
        UNEXPECTED_ERROR: 예상치 못한 에러 (JSON 파싱 실패, 스키마 불일치 등)
    """

    NOT_FOUND = "NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"
    PARTIAL = "PARTIAL"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


# 에러 타입별 사용자 메시지 템플릿
ERROR_RESPONSE_TEMPLATES: Dict[str, str] = {
    PersonalizationErrorType.NOT_FOUND.value: "해당 기간에 조회할 데이터가 없어요.",
    PersonalizationErrorType.TIMEOUT.value: "지금 조회가 지연되고 있어요. 잠시 후 다시 시도해 주세요.",
    PersonalizationErrorType.NETWORK_ERROR.value: "서버 연결에 실패했어요. 잠시 후 다시 시도해 주세요.",
    PersonalizationErrorType.HTTP_ERROR.value: "서버 응답 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
    PersonalizationErrorType.CONFIG_ERROR.value: "시스템 설정 오류가 발생했어요. 관리자에게 문의해 주세요.",
    PersonalizationErrorType.PARTIAL.value: "일부 정보만 가져올 수 있었어요. 가능한 범위에서 정리해 드릴게요.",
    PersonalizationErrorType.NOT_IMPLEMENTED.value: (
        "현재 데모 범위에서는 지원하지 않는 질문이에요. "
        "지원되는 질문 예시: 남은 연차, 복지 포인트 잔액, 미이수 필수 교육, 이번 주 할 일 등"
    ),
    PersonalizationErrorType.UNEXPECTED_ERROR.value: "예상치 못한 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
}


# =============================================================================
# Personalization Error Model
# =============================================================================


class PersonalizationError(BaseModel):
    """개인화 에러 정보.

    Attributes:
        type: 에러 유형 (NOT_FOUND, TIMEOUT, PARTIAL, NOT_IMPLEMENTED)
        message: 에러 메시지
    """

    type: str = Field(..., description="에러 유형")
    message: str = Field(default="", description="에러 메시지")


# =============================================================================
# Personalization Resolve Request/Response
# =============================================================================


class PersonalizationResolveRequest(BaseModel):
    """개인화 조회 요청 (POST /api/personalization/resolve).

    백엔드에 facts를 요청하는 스키마입니다.

    Attributes:
        sub_intent_id: Q1-Q20 인텐트 ID
        period: 기간 유형 (옵션, 기본값 사용 가능)
        target_dept_id: 부서 비교 대상 ID (향후 사용 예정)
        topic: 교육 토픽 (Q2, Q7, Q8, Q18, Q19에서 사용)
    """

    sub_intent_id: str = Field(..., description="Q1-Q20 인텐트 ID")
    period: Optional[str] = Field(default=None, description="기간 유형 (this-week|this-month|3m|this-year)")
    target_dept_id: Optional[str] = Field(default=None, description="부서 비교 대상 ID (Q5에서만 사용)")
    topic: Optional[str] = Field(
        default=None,
        description="교육 토픽 (Q2, Q7, Q8, Q18, Q19에서 사용). "
        "WORKPLACE_BULLYING, SEXUAL_HARASSMENT_PREVENTION, "
        "PERSONAL_INFO_PROTECTION, DISABILITY_AWARENESS, JOB_DUTY 중 하나"
    )


class PersonalizationFacts(BaseModel):
    """개인화 Facts 데이터.

    백엔드에서 반환하는 정답 데이터입니다.
    AI는 이 facts만을 바탕으로 답변을 생성합니다.

    Attributes:
        sub_intent_id: 요청한 인텐트 ID
        period_start: 조회 기간 시작일
        period_end: 조회 기간 종료일
        updated_at: 데이터 최종 업데이트 시각
        metrics: 수치 데이터 (dict)
        items: 목록 데이터 (list)
        extra: 추가 데이터 (dict)
        error: 에러 정보 (있는 경우)
    """

    sub_intent_id: str = Field(..., description="요청한 인텐트 ID")
    period_start: Optional[str] = Field(default=None, description="조회 기간 시작일")
    period_end: Optional[str] = Field(default=None, description="조회 기간 종료일")
    updated_at: Optional[str] = Field(default=None, description="데이터 최종 업데이트 시각")
    metrics: Dict[str, Any] = Field(default_factory=dict, description="수치 데이터")
    items: List[Dict[str, Any]] = Field(default_factory=list, description="목록 데이터")
    extra: Dict[str, Any] = Field(default_factory=dict, description="추가 데이터")
    error: Optional[PersonalizationError] = Field(default=None, description="에러 정보")


# PersonalizationResolveResponse는 PersonalizationFacts와 동일
PersonalizationResolveResponse = PersonalizationFacts


# =============================================================================
# Answer Generator Context
# =============================================================================


class AnswerGeneratorContext(BaseModel):
    """Answer Generator에 전달할 컨텍스트.

    LLM이 facts 기반으로 자연어 답변을 생성할 때 사용합니다.

    Attributes:
        sub_intent_id: 인텐트 ID
        user_question: 사용자 원본 질문
        facts: 백엔드에서 받은 facts 데이터
    """

    sub_intent_id: str = Field(..., description="인텐트 ID")
    user_question: str = Field(..., description="사용자 원본 질문")
    facts: PersonalizationFacts = Field(..., description="백엔드 facts 데이터")


# =============================================================================
# Sub-Intent Metadata (Q1-Q20 메타데이터)
# =============================================================================


class SubIntentMetadata(BaseModel):
    """세부 의도 메타데이터.

    각 Q1-Q20 인텐트에 대한 설명 및 키워드입니다.

    Attributes:
        sub_intent_id: 인텐트 ID
        description: 인텐트 설명
        keywords: 관련 키워드 목록
        default_period: 기본 기간 (옵션)
        domain: 도메인 (EDU, HR, QUIZ 등)
    """

    sub_intent_id: str = Field(..., description="인텐트 ID")
    description: str = Field(..., description="인텐트 설명")
    keywords: List[str] = Field(default_factory=list, description="관련 키워드 목록")
    default_period: Optional[PeriodType] = Field(default=None, description="기본 기간")
    domain: str = Field(default="HR", description="도메인")


# Q1-Q20 메타데이터 정의
SUB_INTENT_METADATA: Dict[str, SubIntentMetadata] = {
    "Q1": SubIntentMetadata(
        sub_intent_id="Q1",
        description="미이수 필수 교육 조회",
        keywords=["미이수", "안 들은", "수강 안 한", "필수 교육", "아직 안 한", "안한 교육"],
        domain="EDU",
    ),
    "Q2": SubIntentMetadata(
        sub_intent_id="Q2",
        description="내 교육 수료 현황 조회",
        keywords=["수료", "이수", "완료한 교육", "들은 교육"],
        domain="EDU",
    ),
    "Q3": SubIntentMetadata(
        sub_intent_id="Q3",
        description="이번 달 데드라인 필수 교육",
        keywords=["이번 달", "마감", "데드라인", "기한", "곧 끝나는"],
        default_period=PeriodType.THIS_MONTH,
        domain="EDU",
    ),
    "Q4": SubIntentMetadata(
        sub_intent_id="Q4",
        description="특정 교육 진도율/시청률 조회",
        keywords=["진도율", "시청률", "얼마나 봤", "몇 퍼센트"],
        domain="EDU",
    ),
    "Q5": SubIntentMetadata(
        sub_intent_id="Q5",
        description="내 퀴즈 평균 vs 부서/전사 평균 비교",
        keywords=["평균 점수", "부서 평균", "전사 평균", "비교", "내 점수 어때"],
        default_period=PeriodType.THIS_YEAR,
        domain="QUIZ",
    ),
    "Q6": SubIntentMetadata(
        sub_intent_id="Q6",
        description="가장 낮은/높은 점수 교육 TOP3",
        keywords=["낮은 점수", "높은 점수", "취약 과목", "못 본 퀴즈", "잘 본 퀴즈", "TOP3"],
        default_period=PeriodType.THIS_YEAR,
        domain="QUIZ",
    ),
    "Q7": SubIntentMetadata(
        sub_intent_id="Q7",
        description="특정 교육 퀴즈 결과 조회",
        keywords=["퀴즈 결과", "퀴즈 점수", "시험 결과"],
        domain="QUIZ",
    ),
    "Q8": SubIntentMetadata(
        sub_intent_id="Q8",
        description="내 퀴즈 점수 이력 조회",
        keywords=["퀴즈 이력", "점수 이력", "퀴즈 기록"],
        domain="QUIZ",
    ),
    "Q9": SubIntentMetadata(
        sub_intent_id="Q9",
        description="이번 주 교육/퀴즈 할 일 1줄 (통합)",
        keywords=["이번 주", "할 일", "해야 할", "TODO", "오늘 할"],
        default_period=PeriodType.THIS_WEEK,
        domain="EDU",
    ),
    "Q10": SubIntentMetadata(
        sub_intent_id="Q10",
        description="내 근태 현황 조회",
        keywords=["근태", "출퇴근", "지각", "조퇴"],
        domain="HR",
    ),
    "Q11": SubIntentMetadata(
        sub_intent_id="Q11",
        description="남은 연차 일수",
        keywords=["연차", "남은 연차", "휴가", "잔여 연차", "연차 몇 개"],
        default_period=PeriodType.THIS_YEAR,
        domain="HR",
    ),
    "Q12": SubIntentMetadata(
        sub_intent_id="Q12",
        description="연차 사용 이력 조회",
        keywords=["연차 사용", "연차 이력", "연차 내역", "휴가 내역"],
        domain="HR",
    ),
    "Q13": SubIntentMetadata(
        sub_intent_id="Q13",
        description="급여 명세서 요약",
        keywords=["급여", "월급", "명세서", "급여 명세"],
        domain="HR",
    ),
    "Q14": SubIntentMetadata(
        sub_intent_id="Q14",
        description="복지/식대 포인트 잔액",
        keywords=["복지 포인트", "식대", "포인트 잔액", "복지비", "포인트 얼마"],
        domain="HR",
    ),
    "Q15": SubIntentMetadata(
        sub_intent_id="Q15",
        description="복지 포인트 사용 내역",
        keywords=["포인트 사용", "복지 내역", "포인트 내역"],
        domain="HR",
    ),
    "Q16": SubIntentMetadata(
        sub_intent_id="Q16",
        description="내 인사 정보 조회",
        keywords=["인사 정보", "내 정보", "프로필"],
        domain="HR",
    ),
    "Q17": SubIntentMetadata(
        sub_intent_id="Q17",
        description="내 팀/부서 정보 조회",
        keywords=["팀 정보", "부서 정보", "우리 팀", "우리 부서"],
        domain="HR",
    ),
    "Q18": SubIntentMetadata(
        sub_intent_id="Q18",
        description="보안 교육 이수 현황",
        keywords=["보안 교육", "보안 이수", "보안 현황"],
        domain="EDU",
    ),
    "Q19": SubIntentMetadata(
        sub_intent_id="Q19",
        description="필수 교육 전체 요약",
        keywords=["필수 교육 요약", "교육 현황 요약", "전체 교육"],
        domain="EDU",
    ),
    "Q20": SubIntentMetadata(
        sub_intent_id="Q20",
        description="올해 HR 할 일 (미완료)",
        keywords=["HR 할 일", "HR TODO", "인사 할 일", "미완료 HR"],
        default_period=PeriodType.THIS_YEAR,
        domain="HR",
    ),
}
