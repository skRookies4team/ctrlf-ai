"""
설정 모듈 (Configuration Module)

pydantic-settings를 사용하여 환경변수 및 .env 파일에서 설정 값을 로드합니다.
싱글턴 패턴으로 설정 인스턴스를 캐싱하여 애플리케이션 전체에서 재사용합니다.

Phase 9 업데이트:
- AI_ENV 환경변수로 mock/real 모드 전환 지원
- mock 모드: Docker Compose 내 Mock 서비스 사용
- real 모드: 실제 RAGFlow/LLM/Backend 서비스 연결

실제 환경변수 이름:
- RAGFLOW_BASE_URL (mock/real 공통) 또는 RAGFLOW_BASE_URL_MOCK/RAGFLOW_BASE_URL_REAL
- LLM_BASE_URL (mock/real 공통) 또는 LLM_BASE_URL_MOCK/LLM_BASE_URL_REAL
- BACKEND_BASE_URL (mock/real 공통) 또는 BACKEND_BASE_URL_MOCK/BACKEND_BASE_URL_REAL
"""

from functools import lru_cache
from typing import Any, Literal, Optional

from pydantic import HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_str_to_none(v: Any) -> Any:
    """빈 문자열을 None으로 변환합니다."""
    if v == "":
        return None
    return v


class Settings(BaseSettings):
    """
    애플리케이션 설정 클래스

    환경변수 또는 .env 파일에서 값을 읽어옵니다.
    기본값이 없는 필드는 반드시 환경변수로 제공해야 합니다.

    Phase 9: mock/real 모드 전환
    - AI_ENV=mock: Mock 서비스 사용 (Docker Compose 통합 테스트용)
    - AI_ENV=real: 실제 서비스 사용 (프로덕션/스테이징용)
    """

    # 앱 기본 정보
    APP_NAME: str = "ctrlf-ai-gateway"
    APP_ENV: str = "local"  # local / dev / prod / docker
    APP_VERSION: str = "0.1.0"

    # 로깅 설정
    LOG_LEVEL: str = "INFO"

    # =========================================================================
    # Phase 9: AI 환경 모드 설정 (mock / real)
    # =========================================================================
    AI_ENV: str = "mock"  # "mock" or "real"

    # Mock 모드 URL (Docker Compose 내부 네트워크 주소)
    RAGFLOW_BASE_URL_MOCK: str = "http://ragflow:8080"
    LLM_BASE_URL_MOCK: str = "http://llm-internal:8001"
    BACKEND_BASE_URL_MOCK: str = "http://backend-mock:8081"

    # Real 모드 URL (실제 서비스 주소, 배포 시 설정)
    # TODO: 실제 서비스 URL이 확정되면 여기에 설정
    RAGFLOW_BASE_URL_REAL: Optional[str] = None
    LLM_BASE_URL_REAL: Optional[str] = None
    BACKEND_BASE_URL_REAL: Optional[str] = None

    # =========================================================================
    # 외부 서비스 URL (기존 호환성 유지)
    # 직접 설정 시 AI_ENV 모드보다 우선됨
    # =========================================================================
    # ctrlf-ragflow 서비스 URL
    RAGFLOW_BASE_URL: Optional[HttpUrl] = None

    # LLM 서비스 URL (OpenAI API 호환 또는 자체 LLM 서버)
    LLM_BASE_URL: Optional[HttpUrl] = None

    # LLM 모델명 (vLLM 등에서 필요)
    LLM_MODEL_NAME: str = "Qwen/Qwen2.5-7B-Instruct"

    # ctrlf-back (Spring 백엔드) 연동 URL
    BACKEND_BASE_URL: Optional[HttpUrl] = None

    # =========================================================================
    # PII 마스킹 서비스 설정
    # =========================================================================
    # PII 서비스 URL (외부 PII 마스킹 서비스, 선택사항)
    PII_BASE_URL: Optional[HttpUrl] = None

    # PII 마스킹 활성화 여부 (True: 마스킹 수행, False: 바이패스)
    PII_ENABLED: bool = True

    # =========================================================================
    # RAGFlow 검색 서비스 설정 (Phase 18)
    # =========================================================================
    # RAGFlow 검색 타임아웃 (초)
    RAGFLOW_TIMEOUT_SEC: float = 10.0

    # RAGFlow API Key (인증 필요 시)
    RAGFLOW_API_KEY: Optional[str] = None

    # Dataset 슬러그 → dataset_id 매핑
    # 형식: "slug1:dataset_id1,slug2:dataset_id2,..."
    # 예: "policy:41e03caccb5f11f0a421a640f6c0fe08"
    RAGFLOW_DATASET_MAPPING: str = "policy:kb_policy_001,training:kb_training_001,incident:kb_incident_001"

    # =========================================================================
    # Phase 19: 개별 KB_ID 설정 (RAGFLOW_DATASET_MAPPING 대신 사용 가능)
    # =========================================================================
    # 각 도메인별 RAGFlow Knowledge Base ID
    RAGFLOW_KB_ID_POLICY: Optional[str] = None
    RAGFLOW_KB_ID_TRAINING: Optional[str] = None
    RAGFLOW_KB_ID_SECURITY: Optional[str] = None
    RAGFLOW_KB_ID_INCIDENT: Optional[str] = None
    RAGFLOW_KB_ID_EDUCATION: Optional[str] = None

    # =========================================================================
    # Phase 20: FAQ 생성 고도화 설정
    # =========================================================================
    # RAGFlow 검색 결과 캐싱 (Phase 20-AI-1)
    FAQ_RAG_CACHE_ENABLED: bool = True  # 캐시 활성화 여부
    FAQ_RAG_CACHE_TTL_SECONDS: int = 300  # 캐시 TTL (초)
    FAQ_RAG_CACHE_MAXSIZE: int = 2048  # 최대 캐시 항목 수

    # 배치 FAQ 생성 동시성 (Phase 20-AI-2)
    FAQ_BATCH_CONCURRENCY: int = 4  # 동시 처리 가능한 요청 수

    # 품질 모니터링 (Phase 20-AI-4)
    FAQ_CONFIDENCE_WARN_THRESHOLD: float = 0.6  # 경고 임계값

    # =========================================================================
    # Phase 21: Intent Router 설정
    # =========================================================================
    # LLM Router 사용 여부 (False면 Rule Router만 사용)
    ROUTER_USE_LLM: bool = True

    # Rule Router만 사용할 최소 신뢰도 (이상이면 LLM Router 스킵)
    ROUTER_RULE_CONFIDENCE_THRESHOLD: float = 0.85

    # 되묻기/확인 대기 만료 시간 (초)
    ROUTER_PENDING_TIMEOUT_SECONDS: int = 300  # 5분

    # =========================================================================
    # Validators: 빈 문자열을 None으로 변환
    # =========================================================================
    @field_validator(
        "RAGFLOW_BASE_URL",
        "LLM_BASE_URL",
        "BACKEND_BASE_URL",
        "PII_BASE_URL",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: Any) -> Any:
        """빈 문자열을 None으로 변환하여 Optional[HttpUrl] 필드 처리."""
        return _empty_str_to_none(v)

    # =========================================================================
    # 인증 설정
    # =========================================================================
    # ctrlf-back 인증 토큰 (선택사항, 설정 시 Authorization: Bearer 헤더로 전송)
    BACKEND_API_TOKEN: Optional[str] = None

    # =========================================================================
    # CORS 설정
    # =========================================================================
    # 허용할 Origin (쉼표로 구분)
    # 개발 환경: * (모든 origin 허용)
    # 프로덕션: https://ctrlf.example.com
    CORS_ORIGINS: str = "*"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # .env에 정의되지 않은 추가 필드 무시
    )

    # =========================================================================
    # Phase 9: mock/real 모드에 따른 URL 자동 선택 프로퍼티
    # =========================================================================

    @property
    def ragflow_base_url(self) -> Optional[str]:
        """
        RAGFlow 서비스 URL을 반환합니다.

        우선순위:
        1. RAGFLOW_BASE_URL이 직접 설정된 경우 그 값 사용
        2. AI_ENV=real이면 RAGFLOW_BASE_URL_REAL 사용
        3. AI_ENV=mock이면 RAGFLOW_BASE_URL_MOCK 사용

        Returns:
            str: RAGFlow 서비스 URL, 미설정 시 None
        """
        if self.RAGFLOW_BASE_URL:
            return str(self.RAGFLOW_BASE_URL)

        if self.AI_ENV == "real":
            if not self.RAGFLOW_BASE_URL_REAL:
                return None  # real 모드인데 URL 미설정
            return self.RAGFLOW_BASE_URL_REAL

        # mock 모드: 빈 문자열이면 None 반환 (테스트 환경 지원)
        if not self.RAGFLOW_BASE_URL_MOCK:
            return None
        return self.RAGFLOW_BASE_URL_MOCK

    @property
    def llm_base_url(self) -> Optional[str]:
        """
        LLM 서비스 URL을 반환합니다.

        우선순위:
        1. LLM_BASE_URL이 직접 설정된 경우 그 값 사용
        2. AI_ENV=real이면 LLM_BASE_URL_REAL 사용
        3. AI_ENV=mock이면 LLM_BASE_URL_MOCK 사용

        Returns:
            str: LLM 서비스 URL, 미설정 시 None
        """
        if self.LLM_BASE_URL:
            return str(self.LLM_BASE_URL)

        if self.AI_ENV == "real":
            if not self.LLM_BASE_URL_REAL:
                return None
            return self.LLM_BASE_URL_REAL

        # mock 모드: 빈 문자열이면 None 반환 (테스트 환경 지원)
        if not self.LLM_BASE_URL_MOCK:
            return None
        return self.LLM_BASE_URL_MOCK

    @property
    def backend_base_url(self) -> Optional[str]:
        """
        Backend 서비스 URL을 반환합니다.

        우선순위:
        1. BACKEND_BASE_URL이 직접 설정된 경우 그 값 사용
        2. AI_ENV=real이면 BACKEND_BASE_URL_REAL 사용
        3. AI_ENV=mock이면 BACKEND_BASE_URL_MOCK 사용

        Returns:
            str: Backend 서비스 URL, 미설정 시 None
        """
        if self.BACKEND_BASE_URL:
            return str(self.BACKEND_BASE_URL)

        if self.AI_ENV == "real":
            if not self.BACKEND_BASE_URL_REAL:
                return None
            return self.BACKEND_BASE_URL_REAL

        # mock 모드: 빈 문자열이면 None 반환 (테스트 환경 지원)
        if not self.BACKEND_BASE_URL_MOCK:
            return None
        return self.BACKEND_BASE_URL_MOCK

    @property
    def is_mock_mode(self) -> bool:
        """Mock 모드인지 확인합니다."""
        return self.AI_ENV == "mock"

    @property
    def is_real_mode(self) -> bool:
        """Real 모드인지 확인합니다."""
        return self.AI_ENV == "real"

    @property
    def ragflow_dataset_to_kb_mapping(self) -> dict[str, str]:
        """
        Dataset 슬러그 → kb_id 매핑을 딕셔너리로 반환합니다.

        RAGFLOW_DATASET_MAPPING 환경변수를 파싱합니다.
        형식: "slug1:kb_id1,slug2:kb_id2,..."

        Returns:
            dict[str, str]: {"policy": "kb_policy_001", ...}
        """
        mapping: dict[str, str] = {}
        if not self.RAGFLOW_DATASET_MAPPING:
            return mapping

        for pair in self.RAGFLOW_DATASET_MAPPING.split(","):
            pair = pair.strip()
            if ":" in pair:
                slug, kb_id = pair.split(":", 1)
                mapping[slug.strip().lower()] = kb_id.strip()

        return mapping


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


def clear_settings_cache() -> None:
    """
    설정 캐시를 클리어합니다.

    테스트 환경에서 환경변수 변경 후 Settings를 다시 로드할 때 사용합니다.
    """
    get_settings.cache_clear()
