"""
AI 로그 모델 모듈 (AI Log Models Module)

백엔드 DB에 저장될 AI 로그 데이터 스키마를 정의합니다.
게이트웨이에서 생성한 Intent/Route/PII 메타데이터를 턴 단위로 저장하여
도메인별/라우트별 질문 비율, PII 검출 비율, RAG 사용 비율 등의 지표를 추출하고
보안/컴플라이언스 확인에 활용합니다.

백엔드 DB 테이블 스키마 참고:
- id, created_at
- user_id, session_id, turn_index, channel, user_role, department
- domain, intent, route
- has_pii_input, has_pii_output
- model_name
- rag_used, rag_source_count
- latency_ms_total
- error_code, error_message
- question_masked, answer_masked (LOG 단계 마스킹 텍스트, nullable)
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AILogEntry(BaseModel):
    """
    AI 로그 엔트리 모델.

    게이트웨이에서 생성되어 백엔드로 전송되는 턴 단위 로그 데이터입니다.
    백엔드에서 DB에 저장할 때 id, created_at은 백엔드에서 생성합니다.

    Attributes:
        session_id: 채팅 세션 ID (백엔드에서 넘긴 값 그대로)
        user_id: 사용자 ID / 사번
        turn_index: 세션 내 턴 인덱스 (0부터 시작, 선택사항)
        channel: 요청 채널 (WEB, MOBILE 등)
        user_role: 사용자 역할 (EMPLOYEE, MANAGER, ADMIN 등)
        department: 사용자 부서 (선택사항)
        domain: 게이트웨이에서 보정한 도메인 (POLICY, INCIDENT, EDUCATION 등)
        intent: 질문 의도 (POLICY_QA, INCIDENT_REPORT, EDUCATION_QA, GENERAL_CHAT 등)
        route: 라우팅 결과 (ROUTE_RAG_INTERNAL, ROUTE_LLM_ONLY, ROUTE_INCIDENT, ROUTE_TRAINING 등)
        model_name: 실제 사용한 LLM 모델 이름
        has_pii_input: 입력(사용자 질문)에서 PII 검출 여부
        has_pii_output: 출력(LLM 응답)에서 PII 검출 여부
        rag_used: RAG 검색 사용 여부
        rag_source_count: RAG로 검색된 문서 개수
        latency_ms: 해당 턴 전체 처리 시간 (ms)
        error_code: 에러 발생 시 에러 코드 (선택사항)
        error_message: 에러 발생 시 에러 메시지 (선택사항)
        question_masked: LOG 단계에서 강하게 마스킹된 질문 텍스트 (선택사항)
        answer_masked: LOG 단계에서 강하게 마스킹된 답변 텍스트 (선택사항)
    """

    model_config = ConfigDict(protected_namespaces=())

    # 세션/사용자 정보
    session_id: str = Field(description="채팅 세션 ID")
    user_id: str = Field(description="사용자 ID / 사번")
    turn_index: Optional[int] = Field(
        default=None,
        description="세션 내 턴 인덱스 (0부터 시작)"
    )
    channel: str = Field(default="WEB", description="요청 채널 (WEB, MOBILE 등)")
    user_role: str = Field(description="사용자 역할 (EMPLOYEE, MANAGER, ADMIN 등)")
    department: Optional[str] = Field(
        default=None,
        description="사용자 부서"
    )

    # 의도/라우팅 정보
    domain: str = Field(description="게이트웨이에서 보정한 도메인")
    intent: str = Field(description="질문 의도 (IntentType)")
    route: str = Field(description="라우팅 결과 (RouteType)")

    # PII 마스킹 정보
    has_pii_input: bool = Field(
        default=False,
        description="입력에서 PII 검출 여부"
    )
    has_pii_output: bool = Field(
        default=False,
        description="출력에서 PII 검출 여부"
    )

    # 모델/RAG 정보
    model_name: Optional[str] = Field(
        default=None,
        description="사용된 LLM 모델 이름"
    )
    rag_used: bool = Field(
        default=False,
        description="RAG 검색 사용 여부"
    )
    rag_source_count: int = Field(
        default=0,
        description="RAG로 검색된 문서 개수"
    )

    # 성능 정보
    latency_ms: int = Field(description="전체 처리 시간 (ms)")

    # 에러 정보 (선택사항)
    error_code: Optional[str] = Field(
        default=None,
        description="에러 코드 (있으면)"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="에러 메시지 (있으면)"
    )

    # LOG 단계 마스킹 텍스트 (선택사항, PII 원문은 절대 저장 안 함)
    question_masked: Optional[str] = Field(
        default=None,
        description="LOG 단계에서 강하게 마스킹된 질문 텍스트"
    )
    answer_masked: Optional[str] = Field(
        default=None,
        description="LOG 단계에서 강하게 마스킹된 답변 텍스트"
    )


class AILogRequest(BaseModel):
    """
    백엔드로 AI 로그를 전송할 때 사용하는 요청 모델.

    단일 로그 엔트리를 감싸는 래퍼입니다.
    """

    log: AILogEntry = Field(description="AI 로그 엔트리")


class AILogResponse(BaseModel):
    """
    백엔드에서 AI 로그 저장 후 반환하는 응답 모델.
    """

    success: bool = Field(description="로그 저장 성공 여부")
    log_id: Optional[str] = Field(
        default=None,
        description="저장된 로그의 ID (성공 시)"
    )
    message: Optional[str] = Field(
        default=None,
        description="응답 메시지"
    )
