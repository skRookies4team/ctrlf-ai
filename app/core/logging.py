"""
로깅 설정 모듈 (Logging Configuration Module)

Python 기본 logging 모듈을 사용하여 애플리케이션 로깅을 설정합니다.
uvicorn의 기본 로거와 충돌하지 않도록 구성되어 있습니다.

로그 포맷: 시간 | 로그레벨 | 로거이름 | 메시지
"""

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings


def setup_logging(settings: "Settings") -> None:
    """
    애플리케이션 로깅을 설정합니다.

    Args:
        settings: 애플리케이션 설정 인스턴스

    설정 내용:
        - 루트 로거의 레벨을 settings.LOG_LEVEL로 설정
        - 콘솔 핸들러 추가 (stdout으로 출력)
        - 통일된 로그 포맷 적용
        - uvicorn 로거들의 레벨도 함께 설정

    사용 예시:
        from app.core.config import get_settings
        from app.core.logging import setup_logging

        settings = get_settings()
        setup_logging(settings)
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # 로그 포맷 정의
    # 시간 | 로그레벨 | 로거이름 | 메시지
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # 기본 포매터 생성
    formatter = logging.Formatter(fmt=log_format, datefmt=date_format)

    # 콘솔 핸들러 설정 (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 기존 핸들러가 있으면 제거 (중복 로그 방지)
    # 단, uvicorn이 이미 설정한 핸들러는 유지
    for handler in root_logger.handlers[:]:
        if not isinstance(handler, logging.StreamHandler):
            root_logger.removeHandler(handler)

    # 루트 로거에 핸들러가 없으면 추가
    if not root_logger.handlers:
        root_logger.addHandler(console_handler)

    # uvicorn 로거 레벨 설정
    # uvicorn은 자체 로거를 사용하므로 레벨만 맞춰줌
    uvicorn_loggers = [
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
    ]
    for logger_name in uvicorn_loggers:
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.setLevel(log_level)

    # 애플리케이션 로거 설정
    app_logger = logging.getLogger("app")
    app_logger.setLevel(log_level)

    # 설정 완료 로그
    app_logger.info(
        f"Logging configured: level={settings.LOG_LEVEL}, "
        f"app={settings.APP_NAME}, env={settings.APP_ENV}"
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
