"""
공용 예외 및 에러 타입 정의 (Core Exceptions Module)

Phase 12: AI Gateway 안정성/품질 Hardening
- Upstream 서비스 에러 타입 표준화
- 커스텀 예외 클래스로 에러 래핑
- ChatMeta 에러 정보 필드와 연동

에러 분류:
- UPSTREAM_TIMEOUT: RAGFlow/LLM/Backend 타임아웃
- UPSTREAM_ERROR: 외부 서비스 5xx/4xx 에러
- BAD_REQUEST: 입력 검증 실패
- INTERNAL_ERROR: 우리 코드 내부 버그

사용 방법:
    from app.core.exceptions import (
        ErrorType,
        UpstreamServiceError,
        ServiceType,
    )

    # 외부 서비스 에러 발생 시
    raise UpstreamServiceError(
        service=ServiceType.LLM,
        error_type=ErrorType.UPSTREAM_TIMEOUT,
        message="LLM timeout after 10s",
    )
"""

from enum import Enum
from typing import Optional


class ErrorType(str, Enum):
    """
    에러 타입 분류.

    Phase 12: ChatMeta.error_type 필드에 사용됩니다.
    """

    # 외부 서비스 타임아웃
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"

    # 외부 서비스 HTTP 에러 (4xx/5xx)
    UPSTREAM_ERROR = "UPSTREAM_ERROR"

    # 입력 검증 실패
    BAD_REQUEST = "BAD_REQUEST"

    # 내부 서비스 에러
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # 알 수 없는 에러
    UNKNOWN = "UNKNOWN"


class ServiceType(str, Enum):
    """
    외부 서비스 타입.

    Upstream 서비스 에러 발생 시 어떤 서비스에서 에러가 났는지 구분합니다.
    """

    RAGFLOW = "RAGFLOW"
    LLM = "LLM"
    BACKEND = "BACKEND"
    PII = "PII"


class UpstreamServiceError(Exception):
    """
    외부 서비스 에러를 래핑하는 커스텀 예외.

    RAGFlow, LLM, Backend 등 외부 서비스 호출 실패 시 발생합니다.
    상위 레벨(ChatService)에서 일관되게 처리할 수 있도록
    서비스 종류, 에러 타입, HTTP 상태 코드 등을 포함합니다.

    Attributes:
        service: 에러가 발생한 서비스 (ServiceType)
        error_type: 에러 분류 (ErrorType)
        message: 에러 메시지 (외부 메시지 그대로 노출 X, 요약만)
        status_code: HTTP 상태 코드 (해당 시)
        is_timeout: 타임아웃 여부
        original_error: 원본 예외 (디버깅용)

    Example:
        try:
            response = await client.get(url, timeout=5.0)
        except httpx.TimeoutException as e:
            raise UpstreamServiceError(
                service=ServiceType.LLM,
                error_type=ErrorType.UPSTREAM_TIMEOUT,
                message="LLM request timeout",
                is_timeout=True,
                original_error=e,
            )
    """

    def __init__(
        self,
        service: ServiceType,
        error_type: ErrorType,
        message: str,
        status_code: Optional[int] = None,
        is_timeout: bool = False,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.service = service
        self.error_type = error_type
        self.message = message
        self.status_code = status_code
        self.is_timeout = is_timeout
        self.original_error = original_error

        # 로그/디버그용 상세 메시지
        detail = f"[{service.value}] {error_type.value}: {message}"
        if status_code:
            detail += f" (HTTP {status_code})"
        if is_timeout:
            detail += " (timeout)"

        super().__init__(detail)

    def __repr__(self) -> str:
        return (
            f"UpstreamServiceError("
            f"service={self.service.value}, "
            f"error_type={self.error_type.value}, "
            f"message='{self.message}', "
            f"status_code={self.status_code}, "
            f"is_timeout={self.is_timeout})"
        )


class BadRequestError(Exception):
    """
    입력 검증 실패 예외.

    사용자 요청의 입력 데이터가 유효하지 않을 때 발생합니다.

    Attributes:
        message: 에러 메시지
        field: 문제가 된 필드명 (선택)
    """

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
    ) -> None:
        self.message = message
        self.field = field
        self.error_type = ErrorType.BAD_REQUEST

        detail = f"Bad Request: {message}"
        if field:
            detail += f" (field: {field})"

        super().__init__(detail)


class InternalServiceError(Exception):
    """
    내부 서비스 에러 예외.

    우리 코드 내부에서 예기치 않은 에러가 발생했을 때 사용합니다.

    Attributes:
        message: 에러 메시지
        original_error: 원본 예외
    """

    def __init__(
        self,
        message: str,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.message = message
        self.original_error = original_error
        self.error_type = ErrorType.INTERNAL_ERROR

        super().__init__(f"Internal Error: {message}")
