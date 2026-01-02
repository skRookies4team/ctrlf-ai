"""
로깅 설정 모듈 (Logging Configuration Module)

ELK 로그 중앙화를 위한 JSON 1라인 포맷 지원.
contextvars에서 RequestContext를 읽어 모든 LogRecord에 자동 주입합니다.

로그 필드:
- @timestamp: ISO8601 포맷
- level: 로그 레벨 (INFO, ERROR 등)
- logger: 로거 이름
- message: 로그 메시지
- trace_id, user_id, dept_id, conversation_id, turn_id: 요청 컨텍스트
- exception_type, stacktrace: 예외 정보 (있을 경우)
"""

import io
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

# Windows 콘솔 UTF-8 인코딩 설정 (한글 깨짐 방지)
if sys.platform == "win32":
    # stdout/stderr를 UTF-8로 재설정
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if TYPE_CHECKING:
    from app.core.config import Settings


# =============================================================================
# RequestContext Filter - contextvars → LogRecord 자동 주입
# =============================================================================

class RequestContextFilter(logging.Filter):
    """
    contextvars에서 RequestContext를 읽어 LogRecord에 주입하는 필터.

    모든 로그에 trace_id, user_id, dept_id, conversation_id, turn_id가
    자동으로 추가됩니다. 값이 없으면 None으로 설정됩니다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """LogRecord에 RequestContext 필드를 주입합니다."""
        # 지연 import (순환 참조 방지)
        from app.telemetry.context import get_request_context

        ctx = get_request_context()

        # 방어 코드: ctx가 None이어도 안전하게 처리
        if ctx is None:
            record.trace_id = None
            record.user_id = None
            record.dept_id = None
            record.conversation_id = None
            record.turn_id = None
        else:
            record.trace_id = ctx.trace_id
            record.user_id = ctx.user_id
            record.dept_id = ctx.dept_id
            record.conversation_id = ctx.conversation_id
            record.turn_id = ctx.turn_id

        return True  # 항상 로그 통과


# =============================================================================
# JSON Formatter - ELK 친화적 1라인 JSON 출력
# =============================================================================

class JsonFormatter(logging.Formatter):
    """
    ELK 친화적 JSON 1라인 포맷터.

    출력 예시:
    {"@timestamp":"2025-01-18T12:34:56.789Z","level":"INFO","logger":"app.services.chat","message":"Processing request","trace_id":"abc123",...}

    예외 발생 시:
    {"@timestamp":"...","level":"ERROR","message":"Error occurred","exception_type":"ValueError","stacktrace":"Traceback..."}
    """

    def format(self, record: logging.LogRecord) -> str:
        """LogRecord를 JSON 1라인으로 포맷팅합니다."""
        # @timestamp: record.created 기반 (버퍼링/지연 출력에도 실제 이벤트 시간 유지)
        event_time = datetime.fromtimestamp(record.created, tz=timezone.utc)
        timestamp = event_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        # 기본 필드
        log_data: dict[str, Any] = {
            "@timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # RequestContext 필드: 항상 존재 (Kibana 필터/시각화 편의)
        # None이어도 필드는 포함 (exists 조건 일관성)
        log_data["trace_id"] = getattr(record, "trace_id", None)
        log_data["user_id"] = getattr(record, "user_id", None)
        log_data["dept_id"] = getattr(record, "dept_id", None)
        log_data["conversation_id"] = getattr(record, "conversation_id", None)
        log_data["turn_id"] = getattr(record, "turn_id", None)

        # 예외 정보 추가
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            if exc_type is not None:
                log_data["exception_type"] = exc_type.__name__
                # stacktrace를 1라인으로 (\\n으로 줄바꿈 표현)
                log_data["stacktrace"] = "".join(
                    traceback.format_exception(exc_type, exc_value, exc_tb)
                ).replace("\n", "\\n")

        # JSON 1라인 출력 (ensure_ascii=False로 한글 유지)
        return json.dumps(log_data, ensure_ascii=False, separators=(",", ":"))


# =============================================================================
# 기존 텍스트 포맷터 (개발 환경용)
# =============================================================================

class TextFormatter(logging.Formatter):
    """
    개발 환경용 텍스트 포맷터.

    출력 예시:
    2025-01-18 12:34:56 | INFO     | app.services.chat | [trace_id=abc123] Processing request
    """

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        """LogRecord를 텍스트로 포맷팅합니다."""
        # trace_id가 있으면 메시지 앞에 추가
        trace_id = getattr(record, "trace_id", None)
        if trace_id:
            record.msg = f"[trace_id={trace_id}] {record.msg}"
        return super().format(record)


# =============================================================================
# 로깅 설정 함수
# =============================================================================

def setup_logging(settings: "Settings") -> None:
    """
    애플리케이션 로깅을 설정합니다.

    Args:
        settings: 애플리케이션 설정 인스턴스

    설정 내용:
        - 루트 로거에 RequestContextFilter 추가 (trace_id 등 자동 주입)
        - JSON 포맷터 적용 (ELK 친화적)
        - uvicorn 로거들도 동일한 포맷 적용
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # 포맷터 선택: production → JSON, 그 외 → Text
    use_json = getattr(settings, "LOG_FORMAT", "json").lower() == "json"
    formatter: logging.Formatter = JsonFormatter() if use_json else TextFormatter()

    # RequestContext 필터 생성
    context_filter = RequestContextFilter()

    # 콘솔 핸들러 설정 (stdout, UTF-8 인코딩)
    # Windows에서 한글이 깨지지 않도록 UTF-8 스트림 사용
    if sys.platform == "win32":
        # UTF-8 인코딩된 TextIOWrapper 사용
        stream = io.TextIOWrapper(
            sys.stdout.buffer,
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
        )
        console_handler = logging.StreamHandler(stream)
    else:
        console_handler = logging.StreamHandler(sys.stdout)

    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)
    console_handler.addFilter(context_filter)

    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 기존 핸들러 모두 제거 (중복 로그 방지)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 새 핸들러 추가
    root_logger.addHandler(console_handler)

    # uvicorn 로거 통일 설정
    # uvicorn 자체 핸들러 제거 후 루트 로거로 전파
    uvicorn_loggers = ["uvicorn", "uvicorn.error", "uvicorn.access"]
    for logger_name in uvicorn_loggers:
        uv_logger = logging.getLogger(logger_name)
        uv_logger.handlers.clear()  # uvicorn 자체 핸들러 제거
        uv_logger.setLevel(log_level)
        uv_logger.propagate = True  # 루트 로거로 전파

    # 애플리케이션 로거 설정
    app_logger = logging.getLogger("app")
    app_logger.setLevel(log_level)

    # httpx 로거 레벨 조정 (너무 verbose)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # 설정 완료 로그
    app_logger.info(
        f"Logging configured: level={settings.LOG_LEVEL}, "
        f"format={'json' if use_json else 'text'}, app={settings.APP_NAME}"
    )


def get_logger(name: str) -> logging.Logger:
    """
    지정된 이름의 로거를 반환합니다.

    Args:
        name: 로거 이름 (보통 __name__ 사용)

    Returns:
        logging.Logger: 설정된 로거 인스턴스

    사용 예시:
        from app.core.logging import get_logger

        logger = get_logger(__name__)
        logger.info("Hello, World!")
    """
    return logging.getLogger(name)
