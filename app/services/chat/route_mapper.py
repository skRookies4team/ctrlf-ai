"""
라우트 매핑 유틸리티 (Route Mapping Utilities)

ChatService에서 사용하는 라우트 타입 간 매핑 함수들을 제공합니다.
순수 함수로 구현되어 있어 상태에 의존하지 않습니다.

Phase 2 리팩토링:
- ChatService._map_tier0_to_intent → map_tier0_to_intent
- ChatService._map_router_route_to_route_type → map_router_route_to_route_type
- ChatService._map_route_type_to_router_route_type → map_route_type_to_router_route_type

Note:
    is_rag_gap_candidate()는 외부 테스트에서 직접 import하므로
    chat_service.py에 그대로 유지합니다.
"""

from typing import Optional

from app.models.intent import IntentType, RouteType
from app.models.router_types import RouterRouteType, Tier0Intent


def map_tier0_to_intent(tier0_intent: Tier0Intent) -> Optional[IntentType]:
    """
    Tier0Intent를 IntentType으로 매핑합니다.

    Args:
        tier0_intent: Tier-0 의도

    Returns:
        Optional[IntentType]: 매핑된 IntentType 또는 None
    """
    mapping = {
        Tier0Intent.POLICY_QA: IntentType.POLICY_QA,
        Tier0Intent.EDUCATION_QA: IntentType.EDUCATION_QA,
        Tier0Intent.BACKEND_STATUS: IntentType.EDU_STATUS,  # 또는 상황에 따라 달라짐
        Tier0Intent.GENERAL_CHAT: IntentType.GENERAL_CHAT,
        Tier0Intent.SYSTEM_HELP: IntentType.SYSTEM_HELP,
        Tier0Intent.UNKNOWN: IntentType.UNKNOWN,
    }
    return mapping.get(tier0_intent)


def map_router_route_to_route_type(
    router_route: RouterRouteType,
) -> Optional[RouteType]:
    """
    RouterRouteType을 RouteType으로 매핑합니다.

    Args:
        router_route: Router 라우트 타입

    Returns:
        Optional[RouteType]: 매핑된 RouteType 또는 None
    """
    mapping = {
        RouterRouteType.RAG_INTERNAL: RouteType.RAG_INTERNAL,
        RouterRouteType.BACKEND_API: RouteType.BACKEND_API,
        RouterRouteType.LLM_ONLY: RouteType.LLM_ONLY,
        RouterRouteType.ROUTE_SYSTEM_HELP: RouteType.LLM_ONLY,  # LLM_ONLY로 처리
        RouterRouteType.ROUTE_UNKNOWN: RouteType.FALLBACK,
    }
    return mapping.get(router_route)


def map_route_type_to_router_route_type(route: RouteType) -> RouterRouteType:
    """
    RouteType을 RouterRouteType으로 매핑합니다 (역방향).

    Phase 39: Answerability 체크에 필요한 역방향 매핑.

    Args:
        route: RouteType

    Returns:
        RouterRouteType: 매핑된 RouterRouteType
    """
    mapping = {
        RouteType.RAG_INTERNAL: RouterRouteType.RAG_INTERNAL,
        RouteType.ROUTE_RAG_INTERNAL: RouterRouteType.RAG_INTERNAL,
        RouteType.BACKEND_API: RouterRouteType.BACKEND_API,
        RouteType.LLM_ONLY: RouterRouteType.LLM_ONLY,
        RouteType.ROUTE_LLM_ONLY: RouterRouteType.LLM_ONLY,
        RouteType.TRAINING: RouterRouteType.LLM_ONLY,
        RouteType.ROUTE_TRAINING: RouterRouteType.LLM_ONLY,
        RouteType.MIXED_BACKEND_RAG: RouterRouteType.RAG_INTERNAL,
        RouteType.INCIDENT: RouterRouteType.BACKEND_API,
        RouteType.ROUTE_INCIDENT: RouterRouteType.BACKEND_API,
        RouteType.FALLBACK: RouterRouteType.ROUTE_UNKNOWN,
        RouteType.ERROR: RouterRouteType.ROUTE_UNKNOWN,
    }
    return mapping.get(route, RouterRouteType.ROUTE_UNKNOWN)
