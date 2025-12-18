"""
Phase 22 수정: API Dependencies

공통 의존성 함수들을 정의합니다.

주요 의존성:
- get_actor_user_id: JWT 또는 body에서 user_id 추출
"""

from typing import Optional

from fastapi import HTTPException, Request, status

from app.core.logging import get_logger

logger = get_logger(__name__)


async def get_actor_user_id(
    request: Request,
    body_user_id: Optional[str] = None,
) -> str:
    """
    요청자의 user_id를 반환합니다.

    우선순위:
    1. JWT claim의 user_id (request.state.user_id)
    2. body의 user_id (JWT 없으면, dev only 경고)

    JWT와 body 둘 다 있고 다르면 403 Forbidden.

    Args:
        request: FastAPI Request 객체
        body_user_id: 요청 바디의 user_id (선택)

    Returns:
        str: user_id

    Raises:
        HTTPException: user_id 불일치 또는 없음
    """
    # JWT에서 user_id 추출 (미들웨어에서 설정된 경우)
    jwt_user_id: Optional[str] = None
    if hasattr(request.state, "user_id"):
        jwt_user_id = request.state.user_id

    # JWT와 body 둘 다 있고 다르면 거부
    if jwt_user_id and body_user_id and jwt_user_id != body_user_id:
        logger.warning(
            f"User ID mismatch: jwt={jwt_user_id}, body={body_user_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User ID mismatch between JWT and request body",
        )

    # JWT user_id 우선
    if jwt_user_id:
        return jwt_user_id

    # body user_id fallback (dev only)
    if body_user_id:
        logger.warning(
            f"Using body user_id without JWT (dev only): {body_user_id}"
        )
        return body_user_id

    # 둘 다 없으면 에러
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="User ID required (via JWT or request body)",
    )


def get_optional_user_id_from_request(request: Request) -> Optional[str]:
    """
    Request에서 선택적으로 user_id를 추출합니다.

    JWT 미들웨어가 설정한 user_id를 반환하고,
    없으면 None을 반환합니다 (에러 발생 안 함).

    Args:
        request: FastAPI Request 객체

    Returns:
        Optional[str]: user_id 또는 None
    """
    if hasattr(request.state, "user_id"):
        return request.state.user_id
    return None
