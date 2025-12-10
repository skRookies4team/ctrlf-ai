"""
의도/라우팅/PII 도메인 모델 (Intent/Route/PII Domain Models)

ChatService 파이프라인에서 사용되는 의도 분류, 라우팅, PII 마스킹 관련
Enum 및 Pydantic 모델을 정의합니다.

이 모델들은 나중에 PII/Intent를 별도 서비스(예: GLiNER-PII 서버, 분류 모델 서버)로
빼더라도 그대로 재사용 가능한 "공통 언어" 역할을 합니다.

Phase 10 업데이트:
- UserRole Enum 추가 (EMPLOYEE, ADMIN, INCIDENT_MANAGER)
- Domain Enum 추가 (POLICY, INCIDENT, EDU)
- RouteType에 BACKEND_API, MIXED_BACKEND_RAG 추가
- IntentResult에 user_role 필드 추가
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


# =============================================================================
# Phase 10: 역할(UserRole) Enum
# =============================================================================


class UserRole(str, Enum):
    """사용자 역할 유형.

    사용자의 권한과 접근 가능한 정보를 결정하는 데 사용됩니다.

    Attributes:
        EMPLOYEE: 일반 직원 - 기본 정책 조회, 본인 관련 정보만 접근
        ADMIN: 관리자 (HR/보안/시스템) - 전체 정책 및 통계 접근 가능
        INCIDENT_MANAGER: 신고관리자 - 사고/위반 사례 상세 접근 가능
    """

    EMPLOYEE = "EMPLOYEE"  # 일반 직원
    ADMIN = "ADMIN"  # 관리자 (HR/보안/시스템 포함)
    INCIDENT_MANAGER = "INCIDENT_MANAGER"  # 신고관리자


# =============================================================================
# Phase 10: 도메인(Domain) Enum
# =============================================================================


class Domain(str, Enum):
    """질문 도메인 유형.

    질문이 속하는 업무 영역을 분류합니다.

    Attributes:
        POLICY: 사규/보안 정책 관련
        INCIDENT: 사고/위반 관련
        EDU: 4대 교육/직무 교육 관련
    """

    POLICY = "POLICY"  # 사규/보안 정책
    INCIDENT = "INCIDENT"  # 사고/위반 관련
    EDU = "EDU"  # 4대 교육/직무 교육


# =============================================================================
# 의도(Intent) Enum
# =============================================================================


class IntentType(str, Enum):
    """사용자 질문의 의도 유형.

    사용자의 질문을 분류하여 적절한 처리 경로를 결정하는 데 사용됩니다.

    Phase 10 업데이트:
    - INCIDENT_QA 추가 (사고 관련 일반 문의 vs 신고)
    - EDU_STATUS 추가 (교육 일정/수료 현황 조회)
    """

    # POLICY 도메인
    POLICY_QA = "POLICY_QA"  # 사규/규정/정책 관련 Q&A

    # INCIDENT 도메인
    INCIDENT_REPORT = "INCIDENT_REPORT"  # 사고/유출/보안사고 신고 시작
    INCIDENT_QA = "INCIDENT_QA"  # 사고 관련 일반 문의 (신고 아님)

    # EDU 도메인
    EDUCATION_QA = "EDUCATION_QA"  # 교육 내용/규정 관련 질문
    EDU_STATUS = "EDU_STATUS"  # 교육 일정/수료/대상 현황 조회

    # 일반
    GENERAL_CHAT = "GENERAL_CHAT"  # 일반 잡담, Small talk
    SYSTEM_HELP = "SYSTEM_HELP"  # 시스템 사용법, 메뉴/화면 설명
    UNKNOWN = "UNKNOWN"  # 분류 불가


# =============================================================================
# 라우트(Route) Enum
# =============================================================================


class RouteType(str, Enum):
    """처리 경로 유형.

    의도 분류 결과에 따라 질문을 처리할 경로를 결정합니다.

    Phase 10 업데이트:
    - BACKEND_API 추가 (백엔드 통계/이력 조회 위주)
    - MIXED_BACKEND_RAG 추가 (백엔드 데이터 + RAG 조합)
    """

    # 주요 처리 경로
    RAG_INTERNAL = "RAG_INTERNAL"  # 내부 RAG + 내부 LLM
    LLM_ONLY = "LLM_ONLY"  # RAG 없이 LLM만 사용
    BACKEND_API = "BACKEND_API"  # 백엔드 API(통계/이력 등) 위주
    MIXED_BACKEND_RAG = "MIXED_BACKEND_RAG"  # 백엔드 데이터 + RAG 조합

    # 특수 경로
    INCIDENT = "INCIDENT"  # 사고/신고 관련 별도 경로
    TRAINING = "TRAINING"  # 교육/퀴즈/영상 생성 관련 경로

    # Fallback/에러 경로
    FALLBACK = "FALLBACK"  # 설정 미비/에러 시 fallback 경로
    ERROR = "ERROR"  # 예외 발생 등 에러 경로

    # Phase 9 이전 호환성 유지용 (deprecated, 추후 제거 예정)
    ROUTE_RAG_INTERNAL = "ROUTE_RAG_INTERNAL"
    ROUTE_LLM_ONLY = "ROUTE_LLM_ONLY"
    ROUTE_INCIDENT = "ROUTE_INCIDENT"
    ROUTE_TRAINING = "ROUTE_TRAINING"
    ROUTE_FALLBACK = "ROUTE_FALLBACK"
    ROUTE_ERROR = "ROUTE_ERROR"


class MaskingStage(str, Enum):
    """PII 마스킹 단계.

    PII 마스킹이 적용되는 시점을 구분합니다.
    각 단계별로 다른 마스킹 전략을 적용할 수 있습니다.
    """

    INPUT = "input"  # 1차: 사용자 입력/업로드 시
    OUTPUT = "output"  # 2차: LLM 응답 출력 직전
    LOG = "log"  # 3차: 로그/학습 데이터 저장 전


class PiiTag(BaseModel):
    """검출된 PII(개인식별정보) 태그.

    PII 마스킹 서비스에서 검출된 개인정보 항목을 표현합니다.

    Attributes:
        entity: 검출된 민감정보 텍스트 (또는 마스킹된 토큰)
        label: 엔티티 타입 (예: "PERSON", "RRN", "PHONE", "EMAIL" 등)
        start: 원문 내 시작 인덱스 (optional)
        end: 원문 내 끝 인덱스 (optional)
    """

    entity: str
    label: str
    start: Optional[int] = None
    end: Optional[int] = None


class PiiMaskResult(BaseModel):
    """PII 마스킹 결과.

    PII 마스킹 서비스의 처리 결과를 표현합니다.

    Attributes:
        original_text: 원본 텍스트
        masked_text: 마스킹된 텍스트 (PII가 없으면 원본과 동일)
        has_pii: PII 검출 여부
        tags: 검출된 PII 태그 목록
    """

    original_text: str
    masked_text: str
    has_pii: bool
    tags: List[PiiTag] = []


class IntentResult(BaseModel):
    """의도 분류 결과.

    IntentService의 분류 결과를 표현합니다.

    Phase 10 업데이트:
    - user_role 필드 추가 (요청에서 전달받은 역할)
    - domain 타입을 Domain Enum으로 변경 가능하도록 Union 처리

    Attributes:
        user_role: 사용자 역할 (IntentService에서 전달받아 그대로 유지)
        intent: 분류된 의도 유형
        domain: 보정된 도메인 (Domain Enum 값 또는 문자열)
        route: 최종 라우팅 결정
    """

    user_role: UserRole
    intent: IntentType
    domain: str  # Domain Enum의 value 또는 레거시 문자열
    route: RouteType
