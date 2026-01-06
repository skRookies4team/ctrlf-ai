"""
LLM 프로바이더 설정 모듈 (LLM Provider Configuration Module)

관리자 대시보드에서 선택한 LLM 모델에 따라 적절한 설정을 반환합니다.

지원 프로바이더:
- exaone: 내부 vLLM 서버 (LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct)
- openai: OpenAI API (gpt-4o-mini 등)

향후 확장 가능:
- claude: Anthropic Claude API
- gemini: Google Gemini API

사용 예시:
    from app.core.llm_providers import get_llm_provider_config, LLMProvider

    config = get_llm_provider_config("openai")
    # {'base_url': 'https://api.openai.com/v1', 'model_name': 'gpt-4o-mini', 'api_key': 'sk-...'}
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LLMProvider(str, Enum):
    """지원하는 LLM 프로바이더 목록."""

    EXAONE = "exaone"
    OPENAI = "openai"
    # 향후 확장
    # CLAUDE = "claude"
    # GEMINI = "gemini"


@dataclass
class LLMProviderConfig:
    """LLM 프로바이더별 설정.

    Attributes:
        base_url: LLM API 엔드포인트 URL
        model_name: 사용할 모델명
        api_key: API 인증 키 (없으면 None)
        provider: 프로바이더 식별자
    """

    base_url: Optional[str]
    model_name: str
    api_key: Optional[str]
    provider: str


def get_llm_provider_config(llm_provider: Optional[str] = None) -> LLMProviderConfig:
    """
    LLM 프로바이더에 따른 설정을 반환합니다.

    Args:
        llm_provider: 프로바이더 식별자 ("exaone", "openai" 등)
                      None이면 기본값(exaone) 사용

    Returns:
        LLMProviderConfig: 해당 프로바이더의 설정

    Examples:
        >>> config = get_llm_provider_config("openai")
        >>> config.base_url
        'https://api.openai.com/v1'
        >>> config.model_name
        'gpt-4o-mini'
    """
    settings = get_settings()

    # OpenAI 프로바이더
    if llm_provider == LLMProvider.OPENAI.value:
        if not settings.OPENAI_API_KEY:
            logger.warning(
                "OpenAI provider selected but OPENAI_API_KEY not configured. "
                "Falling back to EXAONE."
            )
            # API 키 없으면 기본 EXAONE으로 fallback
            return _get_exaone_config(settings)

        return LLMProviderConfig(
            base_url=settings.OPENAI_LLM_BASE_URL,
            model_name=settings.OPENAI_LLM_MODEL,
            api_key=settings.OPENAI_API_KEY,
            provider=LLMProvider.OPENAI.value,
        )

    # 향후 Claude 지원 예시 (주석 처리)
    # if llm_provider == LLMProvider.CLAUDE.value:
    #     return LLMProviderConfig(
    #         base_url="https://api.anthropic.com/v1",
    #         model_name=settings.CLAUDE_MODEL,
    #         api_key=settings.ANTHROPIC_API_KEY,
    #         provider=LLMProvider.CLAUDE.value,
    #     )

    # 기본값: EXAONE (내부 LLM)
    return _get_exaone_config(settings)


def _get_exaone_config(settings) -> LLMProviderConfig:
    """EXAONE(내부 LLM) 설정을 반환합니다."""
    return LLMProviderConfig(
        base_url=settings.llm_base_url,
        model_name=settings.LLM_MODEL_NAME,
        api_key=None,  # 내부 서버는 인증 불필요
        provider=LLMProvider.EXAONE.value,
    )


def get_available_providers() -> list[str]:
    """
    현재 사용 가능한 LLM 프로바이더 목록을 반환합니다.

    API 키가 설정된 프로바이더만 포함됩니다.

    Returns:
        list[str]: 사용 가능한 프로바이더 식별자 목록

    Examples:
        >>> get_available_providers()
        ['exaone', 'openai']
    """
    settings = get_settings()
    providers = [LLMProvider.EXAONE.value]  # EXAONE은 항상 사용 가능

    if settings.OPENAI_API_KEY:
        providers.append(LLMProvider.OPENAI.value)

    # 향후 확장
    # if settings.ANTHROPIC_API_KEY:
    #     providers.append(LLMProvider.CLAUDE.value)

    return providers


def is_valid_provider(llm_provider: Optional[str]) -> bool:
    """
    유효한 LLM 프로바이더인지 확인합니다.

    Args:
        llm_provider: 검증할 프로바이더 식별자

    Returns:
        bool: 유효하면 True
    """
    if llm_provider is None:
        return True  # None은 기본값 사용

    valid_providers = {p.value for p in LLMProvider}
    return llm_provider in valid_providers
