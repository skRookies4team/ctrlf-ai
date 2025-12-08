"""
설정 모듈 (Configuration Module)

pydantic-settings를 사용하여 환경변수 및 .env 파일에서 설정 값을 로드합니다.
싱글턴 패턴으로 설정 인스턴스를 캐싱하여 애플리케이션 전체에서 재사용합니다.

나중에 ctrlf-back(Spring), ctrlf-ragflow, ctrlf-front와 연동 시
필요한 설정들을 이 파일에 추가하면 됩니다.
"""

from functools import lru_cache
from typing import Optional

from pydantic import HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    애플리케이션 설정 클래스

    환경변수 또는 .env 파일에서 값을 읽어옵니다.
    기본값이 없는 필드는 반드시 환경변수로 제공해야 합니다.
    """

    # 앱 기본 정보
    APP_NAME: str = "ctrlf-ai-gateway"
    APP_ENV: str = "local"  # local / dev / prod
    APP_VERSION: str = "0.1.0"

    # 로깅 설정
    LOG_LEVEL: str = "INFO"

    # 외부 서비스 URL (나중에 연동 시 사용)
    # ctrlf-ragflow 서비스 URL
    RAGFLOW_BASE_URL: Optional[HttpUrl] = None

    # LLM 서비스 URL (OpenAI API 호환 또는 자체 LLM 서버)
    LLM_BASE_URL: Optional[HttpUrl] = None

    # ctrlf-back (Spring 백엔드) 연동 URL
    BACKEND_BASE_URL: Optional[HttpUrl] = None

    # CORS 설정 (나중에 ctrlf-front와 연동 시 수정 필요)
    # 현재는 모든 origin 허용, 프로덕션에서는 특정 도메인만 허용하도록 변경
    CORS_ORIGINS: str = "*"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # .env에 정의되지 않은 추가 필드 무시
    )


@lru_cache()
def get_settings() -> Settings:
    """
    설정 인스턴스를 반환합니다.

    lru_cache를 사용하여 싱글턴처럼 동작하며,
    최초 호출 시에만 Settings 인스턴스를 생성합니다.

    Returns:
        Settings: 애플리케이션 설정 인스턴스

    사용 예시:
        from app.core.config import get_settings
        settings = get_settings()
        print(settings.APP_NAME)
    """
    return Settings()
