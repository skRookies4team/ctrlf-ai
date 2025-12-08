"""
Core 패키지

애플리케이션의 핵심 기능을 포함합니다.

모듈:
    - config: 환경 설정 관리 (pydantic-settings 기반)
    - logging: 로깅 설정
"""

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, setup_logging

__all__ = ["Settings", "get_settings", "setup_logging", "get_logger"]
