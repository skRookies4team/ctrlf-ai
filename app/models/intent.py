"""
의도/라우팅/PII 도메인 모델 (Intent/Route/PII Domain Models)

ChatService 파이프라인에서 사용되는 의도 분류, 라우팅, PII 마스킹 관련
Enum 및 Pydantic 모델을 정의합니다.

이 모델들은 나중에 PII/Intent를 별도 서비스(예: GLiNER-PII 서버, 분류 모델 서버)로
빼더라도 그대로 재사용 가능한 "공통 언어" 역할을 합니다.
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class IntentType(str, Enum):
    """사용자 질문의 의도 유형.

    사용자의 질문을 분류하여 적절한 처리 경로를 결정하는 데 사용됩니다.
    """

    POLICY_QA = "POLICY_QA"  # 사규/규정/정책 관련 Q&A
    INCIDENT_REPORT = "INCIDENT_REPORT"  # 사고/유출/보안사고 신고 관련
    EDUCATION_QA = "EDUCATION_QA"  # 교육/훈련/퀴즈 관련 질문
    GENERAL_CHAT = "GENERAL_CHAT"  # 일반 잡담, Small talk
    SYSTEM_HELP = "SYSTEM_HELP"  # 시스템 사용법, 메뉴/화면 설명
    UNKNOWN = "UNKNOWN"  # 분류 불가


class RouteType(str, Enum):
    """처리 경로 유형.

    의도 분류 결과에 따라 질문을 처리할 경로를 결정합니다.
    """

    ROUTE_RAG_INTERNAL = "ROUTE_RAG_INTERNAL"  # 내부 RAG + 내부 LLM
    ROUTE_LLM_ONLY = "ROUTE_LLM_ONLY"  # RAG 없이 LLM만 사용
    ROUTE_INCIDENT = "ROUTE_INCIDENT"  # 사고/신고 관련 별도 경로 (향후 Incident 모듈로 라우트)
    ROUTE_TRAINING = "ROUTE_TRAINING"  # 교육/퀴즈/영상 생성 관련 경로
    ROUTE_FALLBACK = "ROUTE_FALLBACK"  # 설정 미비/에러 시 fallback 경로
    ROUTE_ERROR = "ROUTE_ERROR"  # 예외 발생 등 에러 경로


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

    Attributes:
        intent: 분류된 의도 유형
        domain: ChatRequest.domain 보정용 도메인 (예: "POLICY", "INCIDENT" 등)
        route: 최종 라우팅 결정
    """

    intent: IntentType
    domain: Optional[str] = None
    route: RouteType
