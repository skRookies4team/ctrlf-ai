"""
Chat 서브패키지 (Chat Subpackage)

ChatService의 헬퍼 모듈들을 포함합니다.
Strangler Fig 패턴으로 점진적 분할 진행 중.

Phase 2 리팩토링:
- route_mapper: 라우트 매핑 유틸리티
- response_factory: 응답 생성 팩토리
- rag_handler: RAG 검색 로직
- backend_handler: 백엔드 데이터 조회
- message_builder: LLM 메시지 구성
"""

from app.services.chat.route_mapper import (
    map_tier0_to_intent,
    map_router_route_to_route_type,
    map_route_type_to_router_route_type,
)
from app.services.chat.response_factory import (
    # 상수
    LLM_FALLBACK_MESSAGE,
    BACKEND_FALLBACK_MESSAGE,
    RAG_FAIL_NOTICE,
    MIXED_BACKEND_FAIL_NOTICE,
    SYSTEM_HELP_RESPONSE,
    UNKNOWN_ROUTE_RESPONSE,
    # 함수
    create_fallback_response,
    create_router_response,
    create_system_help_response,
    create_unknown_route_response,
)

__all__: list[str] = [
    # route_mapper
    "map_tier0_to_intent",
    "map_router_route_to_route_type",
    "map_route_type_to_router_route_type",
    # response_factory - 상수
    "LLM_FALLBACK_MESSAGE",
    "BACKEND_FALLBACK_MESSAGE",
    "RAG_FAIL_NOTICE",
    "MIXED_BACKEND_FAIL_NOTICE",
    "SYSTEM_HELP_RESPONSE",
    "UNKNOWN_ROUTE_RESPONSE",
    # response_factory - 함수
    "create_fallback_response",
    "create_router_response",
    "create_system_help_response",
    "create_unknown_route_response",
]
