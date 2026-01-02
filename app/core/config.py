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
    LOG_FORMAT: str = "json"  # "json" (ELK용) 또는 "text" (개발용)

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

    # LLM 모델명 (vLLM 등에서 필요) - 채팅용
    LLM_MODEL_NAME: str = "LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct"

    # 스크립트 생성용 LLM 모델 (미설정 시 LLM_MODEL_NAME 사용)
    SCRIPT_LLM_MODEL: Optional[str] = None

    # 임베딩 서비스 URL (LLM과 분리된 임베딩 서버 사용 시)
    EMBEDDING_BASE_URL: Optional[HttpUrl] = None

    # ctrlf-back (Spring 백엔드) 연동 URL
    BACKEND_BASE_URL: Optional[HttpUrl] = None

    # ctrlf-back infra-service 연동 URL (S3 presigned URL 등)
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
    # 예: "POLICY:사내규정,EDUCATION:정보보안교육,FOUR_MANDATORY:법정의무교육"
    # 실제 값은 .env의 RAGFLOW_DATASET_MAPPING에서 설정
    # 주의: 매핑에 없는 domain은 FAILED 처리됨 (fallback 금지)
    RAGFLOW_DATASET_MAPPING: str = "POLICY:사내규정,EDUCATION:정보보안교육,FOUR_MANDATORY:법정의무교육"

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
    # Step 3: SourceSet 오케스트레이션 설정
    # =========================================================================
    # RAGFlow 파싱 완료 Polling 설정
    RAGFLOW_POLL_INTERVAL_SEC: float = 3.0  # 폴링 간격 (초)
    RAGFLOW_POLL_TIMEOUT_SEC: float = 900.0  # 폴링 타임아웃 (15분)
    RAGFLOW_CHUNK_PAGE_SIZE: int = 1000  # 청크 조회 페이지 크기

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

    # FAQ 생성 검증 설정
    FAQ_INTENT_CONFIDENCE_REQUIRED: bool = False  # 의도 신뢰도 검증 필수 여부 (False면 경고만)
    FAQ_INTENT_CONFIDENCE_THRESHOLD: float = 0.7  # 의도 신뢰도 최소 임계값
    FAQ_LOW_RELEVANCE_BLOCK: bool = False  # LOW_RELEVANCE_CONTEXT 차단 여부 (False면 경고만)

    # =========================================================================
    # Phase 21: Intent Router 설정
    # =========================================================================
    # LLM Router 사용 여부 (False면 Rule Router만 사용)
    ROUTER_USE_LLM: bool = True

    # Rule Router만 사용할 최소 신뢰도 (이상이면 LLM Router 스킵)
    ROUTER_RULE_CONFIDENCE_THRESHOLD: float = 0.85

    # 되묻기/확인 대기 만료 시간 (초)
    ROUTER_PENDING_TIMEOUT_SECONDS: int = 300  # 5분

    # Phase 22: RouterOrchestrator 활성화 여부 (명시적 플래그)
    # True: RouterOrchestrator를 통한 라우팅 + 되묻기/확인 처리
    # False: 기존 IntentService 기반 분류만 사용
    ROUTER_ORCHESTRATOR_ENABLED: bool = False

    # =========================================================================
    # Phase 24: Milvus Vector Database 설정
    # =========================================================================
    # Milvus 서버 연결 정보 (환경변수로 설정 필요)
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530

    # Milvus 컬렉션 설정
    MILVUS_COLLECTION_NAME: str = "ragflow_chunks"

    # 벡터 검색 설정
    MILVUS_TOP_K: int = 5  # 기본 검색 결과 수
    MILVUS_SEARCH_PARAMS: str = '{"metric_type": "COSINE", "params": {"nprobe": 10}}'

    # Milvus 사용 여부 (True면 RAGFlow 대신 Milvus 사용)
    # 기본값: False (RAGFlow 사용). 환경변수로 MILVUS_ENABLED=true 설정 시 Milvus 활성화
    MILVUS_ENABLED: bool = False

    # =========================================================================
    # Option 3: Retrieval Backend 설정 (서비스별 분리)
    # =========================================================================
    # 전역 검색 백엔드 (개별 설정 없을 때 fallback)
    # - ragflow: RAGFlow API를 통한 검색 (기본값)
    # - milvus: Milvus 직접 검색 (Option 3, text도 Milvus에서 조회)
    RETRIEVAL_BACKEND: Literal["ragflow", "milvus"] = "ragflow"

    # 서비스별 검색 백엔드 (설정하지 않으면 RETRIEVAL_BACKEND 사용)
    # 운영 안전: 한 서비스씩 전환하고 문제 시 해당 서비스만 롤백 가능
    FAQ_RETRIEVER_BACKEND: Optional[Literal["ragflow", "milvus"]] = None
    CHAT_RETRIEVER_BACKEND: Optional[Literal["ragflow", "milvus"]] = None
    SCRIPT_RETRIEVER_BACKEND: Optional[Literal["ragflow", "milvus"]] = None

    # 임베딩 계약 검증 (앱 시작 시 dim 불일치 감지)
    # True: dim 불일치 시 서버 기동 실패 (Fail-fast)
    # False: 경고만 출력하고 계속 진행
    EMBEDDING_CONTRACT_STRICT: bool = True

    # Chat 컨텍스트 길이 제한 (Milvus 검색 결과 truncation)
    CHAT_CONTEXT_MAX_CHARS: int = 8000  # 최대 문자 수
    CHAT_CONTEXT_MAX_SOURCES: int = 5   # 최대 소스 수

    # =========================================================================
    # Phase 48/50: Low-relevance Gate 설정
    # =========================================================================
    # L2 거리 기준 (낮을수록 유사함, 0 = 완전 일치)
    # - 0.0~0.8: 매우 유사
    # - 0.8~1.2: 유사
    # - 1.2~1.5: 중간
    # - 1.5 이상: 관련성 낮음
    # min_score(최소 거리)가 이 값보다 크면 low relevance로 판정
    RAG_MAX_L2_DISTANCE: float = 1.5

    # max_score 기준 (유사도 점수, 높을수록 유사함)
    # - 0.55 미만: 관련성 낮음으로 판정
    # max_score가 이 값보다 작으면 low relevance로 판정
    RAG_MIN_MAX_SCORE: float = 0.55

    # 앵커 키워드 게이트용 불용어 (쉼표 구분)
    # Phase 50: 행동 표현은 코드에서 별도 처리 (ACTION_TOKENS, ACTION_SUFFIX_PATTERN)
    # 이 단어들을 제거한 후 남은 토큰이 sources 텍스트에 하나도 없으면 soft 강등
    RAG_ANCHOR_STOPWORDS: str = (
        "관련,규정,정책,문서,있어,없어,어떻게,"
        "무엇,을,를,이,가,은,는,의,에,에서,로,으로,와,과,하고,그리고,"
        "또는,및,대한,대해,대해서,것,수,등,내용,사항,부분,전체,모든,각,해당"
    )

    # domain → dataset_id 매핑 강제 필터 활성화
    RAG_DATASET_FILTER_ENABLED: bool = True

    # =========================================================================
    # Phase 49: EDUCATION dataset_id allowlist 설정
    # =========================================================================
    # EDUCATION 도메인 검색 시 허용할 dataset_id 목록 (쉼표 구분)
    # Milvus 컬렉션의 dataset_id 필드 값과 일치해야 함
    RAG_EDUCATION_DATASET_IDS: str = (
        "정보보안교육,성희롱예방교육,장애인식개선교육,직장내괴롭힘예방교육,개인정보보호교육"
    )

    # =========================================================================
    # Phase 49: Summary Intent 분리 (요약 인텐트)
    # =========================================================================
    # 요약 인텐트 분리 활성화 여부 (기본 OFF)
    # True: "요약해줘", "정리해줘" 등 패턴을 별도 인텐트로 분류
    # False: 기존 로직 유지 (POLICY_QA/EDUCATION_QA로 분류)
    SUMMARY_INTENT_ENABLED: bool = False

    # =========================================================================
    # Phase 50: 금지질문 필터 설정 (Forbidden Query Filter)
    # =========================================================================
    # 금지질문 필터 활성화 여부
    # True: 금지질문 룰셋 검사 후 매칭 시 RAG 검색 스킵
    # False: 금지질문 필터 비활성화 (모든 질문 통과)
    FORBIDDEN_QUERY_FILTER_ENABLED: bool = True

    # 사용할 금지질문 프로필 ("A" = strict, "B" = practical)
    FORBIDDEN_QUERY_PROFILE: str = "A"

    # 금지질문 룰셋 JSON 디렉토리 (app/ 기준 상대경로)
    FORBIDDEN_QUERY_RULESET_DIR: str = "resources/forbidden_queries"

    # Step 4: Fuzzy matching 설정 (rapidfuzz 사용)
    # True: exact miss 시 fuzzy matching 시도
    # False: exact match만 사용
    FORBIDDEN_QUERY_FUZZY_ENABLED: bool = True

    # Fuzzy matching 임계값 (0-100, 높을수록 엄격)
    # 92 이상이면 오탈자/조사 변형 허용, 85 미만은 너무 느슨함
    FORBIDDEN_QUERY_FUZZY_THRESHOLD: int = 92

    # Step 5: Embedding matching 설정 (FAISS 로컬 인덱스)
    # True: fuzzy miss 시 embedding matching 시도
    # False: embedding matching 비활성화 (Step 4까지만 사용)
    # 운영 데이터(미탐/오탐 로그) 충분히 쌓인 후 활성화 권장
    FORBIDDEN_QUERY_EMBEDDING_ENABLED: bool = False

    # Embedding matching 임계값 (0.0-1.0, 코사인 유사도)
    # 0.85 이상이면 의미적으로 유사한 질문 매칭
    FORBIDDEN_QUERY_EMBEDDING_THRESHOLD: float = 0.85

    # Embedding matching 후보 수 (top-K)
    FORBIDDEN_QUERY_EMBEDDING_TOP_K: int = 3

    # Step 6: Embedding provider 검증 설정
    # 로컬 임베딩만 허용 (원격 API 호출 차단)
    FORBIDDEN_QUERY_EMBEDDING_REQUIRE_LOCAL: bool = True

    # FAISS 인덱스 필수 여부 (brute-force 대신 FAISS 강제)
    # True이면 FAISS 미설치 시 embedding 기능 OFF
    FORBIDDEN_QUERY_EMBEDDING_REQUIRE_INDEX: bool = False

    # 룰 개수 임계치 (이 이상이면 brute-force 경고 또는 embedding OFF)
    # 0이면 비활성화
    FORBIDDEN_QUERY_EMBEDDING_RULE_COUNT_THRESHOLD: int = 1000

    # Embedding 모델 설정 (vLLM 서버에서 사용)
    EMBEDDING_MODEL_NAME: str = "BAAI/bge-m3"
    EMBEDDING_DIMENSION: int = 1024  # BGE-M3 기본 차원

    # =========================================================================
    # OpenAI API 설정 (임베딩용)
    # =========================================================================
    # RAGFlow가 OpenAI text-embedding-3-large로 인덱싱한 경우,
    # 검색 시에도 같은 모델로 쿼리 임베딩 필요
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_EMBED_MODEL: str = "text-embedding-3-large"
    OPENAI_EMBED_DIM: int = 3072

    # =========================================================================
    # A/B 테스트: SRoberta 임베딩 설정
    # =========================================================================
    # SRoberta 임베딩 모델 (sentence-transformers 호환)
    SROBERTA_EMBED_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    SROBERTA_EMBED_DIM: int = 384

    # SRoberta 임베딩 서버 URL (별도 서버 사용 시)
    SROBERTA_EMBED_URL: Optional[str] = None

    # SRoberta용 Milvus 컬렉션
    MILVUS_COLLECTION_SROBERTA: str = "ragflow_chunks_sroberta"

    # A/B 테스트 기본 모델 (openai | sroberta)
    # 명시적으로 설정하지 않은 경우 사용할 기본 모델
    AB_DEFAULT_MODEL: str = "openai"

    # Milvus 인증 (선택, 보안 설정된 Milvus 서버 사용 시)
    MILVUS_USER: Optional[str] = None
    MILVUS_PASSWORD: Optional[str] = None

    # =========================================================================
    # Phase 25: 문서 인덱싱 설정 (Internal RAG)
    # =========================================================================
    # 청킹 파라미터
    CHUNK_SIZE: int = 512  # 청크 크기 (토큰 또는 문자)
    CHUNK_OVERLAP: int = 50  # 청크 간 오버랩

    # 인덱싱 재시도 설정
    INDEX_RETRY_MAX_ATTEMPTS: int = 3  # 최대 재시도 횟수
    INDEX_RETRY_BACKOFF_SECONDS: str = "1,2,4"  # 재시도 간격 (초, 쉼표 구분)

    # 파일 다운로드 설정
    FILE_DOWNLOAD_TIMEOUT_SEC: float = 60.0  # 파일 다운로드 타임아웃 (초)
    FILE_MAX_SIZE_MB: int = 50  # 최대 파일 크기 (MB)

    # 지원 파일 형식
    SUPPORTED_FILE_EXTENSIONS: str = ".pdf,.txt,.docx,.doc,.hwp"

    # =========================================================================
    # Phase 29: KB Index 토큰 기반 청킹 설정
    # =========================================================================
    # 교육 스크립트 청크 최대 토큰 수 (초과 시 분할)
    KB_CHUNK_MAX_TOKENS: int = 500

    # 청크 분할 시 최소 토큰 수 (너무 작은 청크 방지)
    KB_CHUNK_MIN_TOKENS: int = 50

    # 토큰 계산 방식 ("char" = 문자 기반 근사, "tiktoken" = 정확한 토큰 계산)
    KB_CHUNK_TOKENIZER: str = "char"

    # 문자 기반 토큰 근사 비율 (한국어: 약 1.5자 = 1토큰)
    KB_CHUNK_CHARS_PER_TOKEN: float = 1.5

    # =========================================================================
    # Phase 39: Answer Guard 설정 (답변 품질 가드레일)
    # =========================================================================
    # 디버그 모드 활성화 (CLI 출력에 route/retrieval/answerable 정보 표시)
    ANSWER_GUARD_DEBUG: bool = False

    # =========================================================================
    # Phase 32: Video Rendering 설정
    # =========================================================================
    # TTS Provider 선택 (mock, gtts, polly, gcp)
    TTS_PROVIDER: str = "gtts"

    # =========================================================================
    # Phase 40: Scene Audio 설정 (문장 단위 TTS + 캡션 타임라인)
    # =========================================================================
    # 씬 끝 무음 패딩 시간 (초)
    SCENE_SILENCE_PADDING_SEC: float = 0.5

    # TTS 문장 최대 길이 (초과 시 분할)
    TTS_MAX_SENTENCE_LENGTH: int = 300

    # Storage Provider 선택 (local, s3, minio)
    STORAGE_PROVIDER: str = "local"

    # 렌더링 출력 디렉토리 (임시 파일용)
    RENDER_OUTPUT_DIR: str = "./video_output"

    # =========================================================================
    # Phase 34: Storage Provider 설정 (영구 저장소)
    # =========================================================================
    # 로컬 Storage 디렉토리 (STORAGE_PROVIDER=local 일 때)
    STORAGE_LOCAL_DIR: str = "./data/assets"

    # Storage Public Base URL (FE가 접근하는 URL)
    # - local: FastAPI StaticFiles로 서빙 (/assets/...)
    # - s3/minio: 외부 접근 가능한 URL
    STORAGE_PUBLIC_BASE_URL: Optional[str] = None

    # S3 설정 (STORAGE_PROVIDER=s3 일 때)
    AWS_S3_BUCKET: Optional[str] = None
    AWS_S3_REGION: str = "ap-northeast-2"
    AWS_S3_PREFIX: str = ""  # Phase 34: object_key에 prefix 포함하므로 빈값
    S3_ENDPOINT_URL: Optional[str] = None  # MinIO 호환 S3 엔드포인트

    # MinIO 설정 (STORAGE_PROVIDER=minio 일 때)
    MINIO_ENDPOINT: Optional[str] = None
    MINIO_BUCKET: str = "videos"

    # =========================================================================
    # Phase 35: Backend Presigned Storage 설정 (STORAGE_PROVIDER=backend_presigned)
    # =========================================================================
    # AI 서버는 AWS 자격증명 없이 백엔드가 발급한 Presigned URL로 S3에 업로드.
    # 최소권한 원칙: AI 서버는 S3 직접 접근 불가, 백엔드 API만 호출.

    # 백엔드 서비스 토큰 (내부 API 인증용)
    BACKEND_SERVICE_TOKEN: Optional[str] = None

    # Presigned URL 발급 API 경로
    BACKEND_STORAGE_PRESIGN_PATH: str = "/internal/storage/presign-put"

    # 업로드 완료 콜백 API 경로
    BACKEND_STORAGE_COMPLETE_PATH: str = "/internal/storage/complete"

    # 업로드 최대 용량 제한 (bytes) - 기본 100MB
    VIDEO_MAX_UPLOAD_BYTES: int = 104857600

    # =========================================================================
    # Phase 36: Presigned 업로드 안정화 설정
    # =========================================================================
    # 재시도 정책 (5xx, 네트워크 오류에만 적용, 4xx는 즉시 실패)
    STORAGE_UPLOAD_RETRY_MAX: int = 3  # 최대 재시도 횟수 (총 4번 시도)
    STORAGE_UPLOAD_RETRY_BASE_SEC: float = 1.0  # exponential backoff 기본 시간

    # ETag 검증 정책 (기본: 엄격 - ETag 없으면 실패)
    # True로 설정하면 ETag 없이도 업로드 성공으로 처리 (DEV 환경용)
    STORAGE_ETAG_OPTIONAL: bool = False

    # =========================================================================
    # Phase 37: Video Visual Style 설정
    # =========================================================================
    # 영상 시각 스타일 (basic: 단색 배경+텍스트, animated: 씬 이미지+Ken Burns+fade)
    VIDEO_VISUAL_STYLE: str = "basic"  # "basic" or "animated"

    # Animated 모드 설정
    VIDEO_WIDTH: int = 1920  # 영상 너비 (animated 모드)
    VIDEO_HEIGHT: int = 1080  # 영상 높이 (animated 모드)
    VIDEO_FPS: int = 30  # 프레임 레이트
    VIDEO_FADE_DURATION: float = 0.5  # 씬 전환 fade 시간 (초)
    VIDEO_KENBURNS_ZOOM: float = 1.1  # Ken Burns 줌 비율 (1.0 = 줌 없음)

    # =========================================================================
    # Phase 38: Script Snapshot on Job Start
    # =========================================================================
    # Job 시작 시 백엔드에서 render-spec을 조회하여 스냅샷으로 저장
    # 이후 TTS/렌더/업로드는 이 스냅샷만 사용 (편집 후에도 기존 잡은 영향 없음)

    # 백엔드 내부 API 인증 토큰 (X-Internal-Token 헤더)
    # Backend → AI 요청 시 사용 (예: /internal/ai/rag-documents/ingest)
    BACKEND_INTERNAL_TOKEN: Optional[str] = None

    # RAGFlow 콜백 전용 인증 토큰 (X-Internal-Token 헤더)
    # RAGFlow → AI 콜백 요청 시 사용 (예: /internal/ai/callbacks/ragflow/ingest)
    # 보안: Backend 토큰과 분리하여 토큰 유출 시 피해 범위 제한
    RAGFLOW_CALLBACK_TOKEN: Optional[str] = None

    # 백엔드 API 타임아웃 (초)
    BACKEND_TIMEOUT_SEC: float = 30.0

    # 스트리밍 채팅 LLM 타임아웃 (초)
    # 백엔드 SSE 타임아웃(보통 60초)보다 길게 설정 권장 (기본값: 180초)
    CHAT_STREAM_LLM_TIMEOUT_SEC: float = 180.0

    # 씬 기본 duration (duration_sec <= 0일 때 사용)
    SCENE_DEFAULT_DURATION_SEC: float = 5.0

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
    def embedding_base_url(self) -> Optional[str]:
        """
        임베딩 서비스 URL을 반환합니다.

        우선순위:
        1. EMBEDDING_BASE_URL이 직접 설정된 경우 그 값 사용
        2. 미설정 시 llm_base_url 사용 (기존 동작 호환)

        Returns:
            str: 임베딩 서비스 URL, 미설정 시 llm_base_url
        """
        if self.EMBEDDING_BASE_URL:
            return str(self.EMBEDDING_BASE_URL)
        return self.llm_base_url

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
    def infra_base_url(self) -> Optional[str]:
        """
        Infra 서비스 URL을 반환합니다 (S3 presigned URL 등).

        Returns:
            str: Infra 서비스 URL, 미설정 시 None
        """
        if self.INFRA_BASE_URL:
            return str(self.INFRA_BASE_URL).rstrip("/")
        return None

    @property
    def is_mock_mode(self) -> bool:
        """Mock 모드인지 확인합니다."""
        return self.AI_ENV == "mock"

    @property
    def is_real_mode(self) -> bool:
        """Real 모드인지 확인합니다."""
        return self.AI_ENV == "real"

    # =========================================================================
    # Option 3: 서비스별 Retriever Backend 프로퍼티
    # =========================================================================

    @property
    def faq_retriever_backend(self) -> str:
        """FAQ 서비스의 검색 백엔드를 반환합니다."""
        return self.FAQ_RETRIEVER_BACKEND or self.RETRIEVAL_BACKEND

    @property
    def chat_retriever_backend(self) -> str:
        """Chat 서비스의 검색 백엔드를 반환합니다."""
        return self.CHAT_RETRIEVER_BACKEND or self.RETRIEVAL_BACKEND

    @property
    def script_retriever_backend(self) -> str:
        """Script 생성 서비스의 검색 백엔드를 반환합니다."""
        return self.SCRIPT_RETRIEVER_BACKEND or self.RETRIEVAL_BACKEND

    @property
    def ragflow_dataset_to_kb_mapping(self) -> dict[str, str]:
        """
        Dataset 슬러그 → dataset_id 매핑을 딕셔너리로 반환합니다.

        RAGFLOW_DATASET_MAPPING 또는 MILVUS_DATASET_MAPPING 환경변수를 파싱합니다.
        형식: "slug1:dataset_id1,slug2:dataset_id2,..."

        Returns:
            dict[str, str]: {"policy": "사내규정", "education": "정보보안교육", ...}
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

    # =========================================================================
    # Phase 34: Storage URL 프로퍼티
    # =========================================================================

    @property
    def storage_public_base_url(self) -> str:
        """Storage Public Base URL을 반환합니다.

        STORAGE_PUBLIC_BASE_URL이 설정되면 그 값을 사용하고,
        없으면 로컬 모드에서 /assets 경로를 기본값으로 사용합니다.
        """
        if self.STORAGE_PUBLIC_BASE_URL:
            return self.STORAGE_PUBLIC_BASE_URL.rstrip("/")
        # 로컬 모드 기본값: StaticFiles 마운트 경로
        return "/assets"


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
