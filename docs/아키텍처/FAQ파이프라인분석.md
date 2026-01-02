# FAQ 파이프라인 분석 문서

> **작성일**: 2025-12-31
> **분석 대상**: ctrlf-ai FAQ 생성 파이프라인
> **분석 방법**: 실제 코드 기반 분석
> **주의**: RAGFlow는 제거되었습니다. Milvus만 사용합니다.

---

## 목차

1. [End-to-End 시스템 플로우](#1-end-to-end-시스템-플로우)
2. [백엔드-AI 서버 역할 분담](#2-백엔드-ai-서버-역할-분담)
3. [AI 서버 아키텍처](#3-ai-서버-아키텍처)
4. [데이터 모델](#4-데이터-모델)
5. [핵심 의존성 흐름](#5-핵심-의존성-흐름)
6. [상세 플로우](#6-상세-플로우)
7. [단계별 데이터 변환 상세](#7-단계별-데이터-변환-상세)
8. [배치 처리](#8-배치-처리)
9. [에러 케이스](#9-에러-케이스)
10. [외부 서비스 연동](#10-외부-서비스-연동)
11. [캐시 및 설정](#11-캐시-및-설정)
12. [데이터 크기 변화 요약](#12-데이터-크기-변화-요약)

---

## 1. End-to-End 시스템 플로우

> FAQ 파이프라인의 전체 흐름입니다. 백엔드(Spring)와 AI 서버(FastAPI)가 협력하여 동작합니다.

### 1.1 전체 플로우 다이어그램

```
[서비스 시작]
초기 데이터 삽입 (9개 FAQ) ─── Flyway 마이그레이션
       │
       ▼
[일상 운영]
사용자 질문 → chat_message 테이블에 로그 수집
       │
       ▼
[주기적 실행 - 예: 주 1회]
AI 분석 → 후보 선정 → 초안 생성
       │
       ▼
[관리자 검토]
Draft 승인/반려
       │
       ▼
[승인 시]
FAQ 추가 → 사용자에게 노출
       │
       ▼
[필요시]
관리자 수동 FAQ 추가
```

### 1.2 단계별 상세 설명

#### 1단계: 초기 데이터 삽입

- **담당**: 백엔드 (Spring)
- **방식**: Flyway 마이그레이션으로 서비스 시작 시 자동 삽입
- **데이터**: 기본 FAQ 9개
  - SECURITY: 3개
  - POLICY: 3개
  - EDUCATION: 3개
- **결과**: 즉시 사용자에게 노출

#### 2단계: 프론트 챗봇 화면에 FAQ 표시

- **담당**: 백엔드 (Spring)
- **API**: `GET /chat/faq` → 활성화된 FAQ 조회
- **프론트 동작**:
  - FAQ 카드로 표시
  - 사용자가 클릭하면 질문이 자동 입력

#### 3단계: 사용자 질문 로그 수집

- **담당**: 백엔드 (Spring)
- **저장소**: `chat_message` 테이블
- **저장 데이터**:
  - 질문 내용
  - 키워드
  - 시각
  - 도메인
- **목적**: AI 분석용 데이터 축적

#### 4단계: AI가 후보 선정 및 초안 생성

- **트리거**: 관리자가 `POST /admin/faq/candidates/auto-generate` 실행
- **역할 분담**: 아래 [2. 백엔드-AI 서버 역할 분담](#2-백엔드-ai-서버-역할-분담) 참조

```
[백엔드 담당]                              [AI 서버 담당]
POST /admin/faq/candidates/auto-generate
         │
         ▼
   질문 로그 분석 (chat_message)
   빈도 분석 (7일/30일)
   클러스터링 → 후보 선정
   faq_candidate 테이블 저장
         │
         │  POST /ai/faq/generate/batch
         │  (cluster_id, canonical_question,
         │   sample_questions, top_docs 전달)
         ▼
                                          PII 검사 (입력)
                                          의도 신뢰도 검증
                                          Milvus 검색 (or top_docs 사용)
                                          PII 검사 (컨텍스트)
                                          LLM 호출 → 초안 생성
                                          PII 검사 (출력)
         │                                         │
         │◀────── FaqDraft 응답 반환 ◀─────────────┘
         ▼
   faq_drafts 테이블 저장
```

#### 5단계: 관리자 승인 → FAQ 추가

- **담당**: 백엔드 (Spring)
- **API**:
  - Draft 목록 조회: `GET /admin/faq/drafts`
  - 승인: `POST /admin/faq/drafts/{id}/approve`
    - FAQ 테이블에 저장
    - 즉시 사용자에게 노출
  - 반려: `POST /admin/faq/drafts/{id}/reject`
    - Draft 상태만 REJECTED로 변경

#### 6단계: 필요시 관리자가 수동으로 FAQ 추가

- **담당**: 백엔드 (Spring)
- **API**: `POST /chat/faq`
- **특징**:
  - AI 초안 과정 없이 바로 추가
  - 즉시 사용자에게 노출

---

## 2. 백엔드-AI 서버 역할 분담

> AI 서버(FastAPI)는 **"초안 생성"만 담당**하고, 나머지 모든 분석/저장/관리는 백엔드(Spring)에서 처리합니다.

### 2.1 역할 분담표

| 기능 | 백엔드 (Spring) | AI 서버 (FastAPI) |
|------|:---------------:|:-----------------:|
| 초기 FAQ 데이터 삽입 (Flyway) | ✅ | ❌ |
| FAQ 조회 API (`GET /chat/faq`) | ✅ | ❌ |
| 사용자 질문 로그 수집 (`chat_message`) | ✅ | ❌ |
| 질문 빈도 분석 (7일/30일) | ✅ | ❌ |
| 후보 선정 (클러스터링) | ✅ | ❌ |
| `faq_candidate` 테이블 저장 | ✅ | ❌ |
| **LLM으로 FAQ 초안 생성** | ❌ | ✅ |
| **PII 검사 (입력/컨텍스트/출력)** | ❌ | ✅ |
| **의도 신뢰도 검증** | ❌ | ✅ |
| `faq_drafts` 테이블 저장 | ✅ | ❌ |
| 관리자 승인/반려 관리 | ✅ | ❌ |
| 최종 FAQ 저장 및 노출 관리 | ✅ | ❌ |
| 수동 FAQ 추가 | ✅ | ❌ |

### 2.2 AI 서버 API 엔드포인트

| 엔드포인트 | 설명 |
|------------|------|
| `POST /ai/faq/generate` | 단건 FAQ 초안 생성 |
| `POST /ai/faq/generate/batch` | 배치 FAQ 초안 생성 |

### 2.3 백엔드 → AI 서버 요청 데이터

```json
{
  "domain": "SEC_POLICY",
  "cluster_id": "cluster-2024-001",
  "canonical_question": "USB 메모리 반출 시 어떤 절차가 필요한가요?",
  "sample_questions": [
    "USB 반출하려면 어떻게 해요?",
    "외부 저장장치 가져가도 되나요?"
  ],
  "top_docs": [
    {
      "doc_id": "DOC-SEC-001",
      "title": "정보보안 관리규정",
      "snippet": "제3장 제2조 (외부저장매체 관리)..."
    }
  ],
  "avg_intent_confidence": 0.85
}
```

### 2.4 AI 서버 → 백엔드 응답 데이터

```json
{
  "status": "SUCCESS",
  "faq_draft": {
    "faq_draft_id": "a1b2c3d4-...",
    "domain": "SEC_POLICY",
    "cluster_id": "cluster-2024-001",
    "question": "USB 메모리를 반출할 때 어떤 절차가 필요한가요?",
    "answer_markdown": "**정보보호팀의 사전 승인이 필요합니다.**\n\n- 반출 신청서 작성...",
    "summary": "정보보호팀의 사전 승인을 받고 반출 신청서를 제출해야 합니다.",
    "answer_source": "MILVUS",
    "ai_confidence": 0.91,
    "created_at": "2025-12-31T10:30:45Z"
  },
  "error_message": null
}
```

---

## 3. AI 서버 아키텍처

> **중요**: RAGFlow 클라이언트는 제거되었습니다. Milvus 직접 검색만 사용합니다.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Backend (Spring)                                │
│                         백엔드가 FAQ 클러스터 정보 전달                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      FastAPI (app/main.py)                                   │
│                         /ai/faq/...                                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     app/api/v1/faq.py                                        │
│  ┌──────────────────────────┐   ┌──────────────────────────────────────┐    │
│  │ POST /ai/faq/generate    │   │ POST /ai/faq/generate/batch          │    │
│  │ (단건 FAQ 생성)          │   │ (배치 FAQ 생성, 동시성 제한)           │    │
│  └──────────────────────────┘   └──────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     FaqDraftService                                          │
│                (app/services/faq_service.py)                                 │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    generate_faq_draft() 메인 플로우                     │ │
│  │                                                                        │ │
│  │  [1] 입력 PII 검사 ─┬─ PII 발견 ──→ FaqGenerationError("PII_DETECTED") │ │
│  │         │           │                                                  │ │
│  │         ▼           │                                                  │ │
│  │  [2] 문서 컨텍스트 확보 ─┬─ top_docs 있음 ──→ 그대로 사용              │ │
│  │         │               │                                              │ │
│  │         │               └─ top_docs 없음 ──→ Milvus 검색               │ │
│  │         │                       │                                      │ │
│  │         │                       ▼                                      │ │
│  │         │               MilvusSearchClient.search()                    │ │
│  │         │                       │                                      │ │
│  │         │                       ├── 결과 없음 → "NO_DOCS_FOUND"        │ │
│  │         │                       │                                      │ │
│  │         │                       ▼                                      │ │
│  │         │               [3] 컨텍스트 PII 검사                          │ │
│  │         │                       │                                      │ │
│  │         │                       ├── PII 발견 → "PII_DETECTED_CONTEXT"  │ │
│  │         │                       │                                      │ │
│  │         ▼                       ▼                                      │ │
│  │  [4] LLM 메시지 구성 (시스템 프롬프트 + 유저 프롬프트)                  │ │
│  │         │                                                              │ │
│  │         ▼                                                              │ │
│  │  [5] LLM 호출 (LLMClient.generate_chat_completion)                     │ │
│  │         │                                                              │ │
│  │         ▼                                                              │ │
│  │  [6] 응답 파싱 (필드별 텍스트 or JSON)                                  │ │
│  │         │                                                              │ │
│  │         ▼                                                              │ │
│  │  [7] 출력 PII 검사 ─── PII 발견 ──→ "PII_DETECTED_OUTPUT"              │ │
│  │         │                                                              │ │
│  │         ▼                                                              │ │
│  │  [8] LOW_RELEVANCE 체크 ─── status=LOW_RELEVANCE                       │ │
│  │         │                      ──→ "LOW_RELEVANCE_CONTEXT"             │ │
│  │         ▼                                                              │ │
│  │  [9] FaqDraft 생성                                                     │ │
│  │         │                                                              │ │
│  │         ▼                                                              │ │
│  │  [10] 품질 모니터링 로그                                               │ │
│  │         │                                                              │ │
│  │         ▼                                                              │ │
│  │       FaqDraft 반환                                                    │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 데이터 모델

### 2.1 요청/응답 모델 (app/models/faq.py)

```
┌─────────────────────────────────────────────────────────────────┐
│                    FaqDraftGenerateRequest                       │
├─────────────────────────────────────────────────────────────────┤
│  domain: str              # 도메인 (SEC_POLICY, PII_PRIVACY)     │
│  cluster_id: str          # FAQ 클러스터 ID                      │
│  canonical_question: str  # 대표 질문                            │
│  sample_questions: List[str]  # 실제 직원 질문 예시              │
│  top_docs: List[FaqSourceDoc]  # 백엔드 제공 후보 문서 (선택)    │
│  avg_intent_confidence: Optional[float]  # 평균 의도 신뢰도      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         FaqDraft                                 │
├─────────────────────────────────────────────────────────────────┤
│  faq_draft_id: str        # UUID                                 │
│  domain: str              # 도메인                               │
│  cluster_id: str          # 클러스터 ID                          │
│  question: str            # 최종 FAQ 질문 문구                   │
│  answer_markdown: str     # FAQ 답변 (마크다운)                  │
│  summary: str (≤120자)    # 한 줄 요약                           │
│  source_doc_id: str       # 근거 문서 ID                         │
│  source_article_label: str # 근거 조항 라벨                      │
│  answer_source: Literal   # "TOP_DOCS" | "MILVUS"                │
│  ai_confidence: float     # AI 신뢰도 (0~1)                      │
│  created_at: datetime     # 생성 시각                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  FaqDraftGenerateResponse                        │
├─────────────────────────────────────────────────────────────────┤
│  status: "SUCCESS" | "FAILED"                                    │
│  faq_draft: FaqDraft (성공 시)                                   │
│  error_message: str (실패 시)                                    │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 answer_source 값 (현재 사용)

| 값         | 설명                            |
| ---------- | ------------------------------- |
| `TOP_DOCS` | 백엔드에서 제공한 top_docs 사용 |
| `MILVUS`   | Milvus 직접 검색 결과 사용      |

> **Note**: `RAGFLOW`는 더 이상 사용되지 않습니다. 레거시 호환을 위해 Literal에 남아있습니다.

### 2.3 내부 데이터 모델

```python
# Milvus 검색 결과를 RagSearchResult로 변환 (faq_service.py)
@dataclass
class RagSearchResult:
    title: Optional[str]      # 문서 제목 (doc_id 사용)
    page: Optional[int]       # chunk_id
    score: float              # 유사도 점수 (0~1)
    snippet: str              # 문서 발췌 (최대 500자)

# PII 마스킹 결과 (app/models/intent.py)
@dataclass
class PiiMaskResult:
    original_text: str        # 원본 텍스트
    masked_text: str          # 마스킹된 텍스트
    has_pii: bool             # PII 검출 여부
    tags: List[PiiTag]        # 검출된 PII 태그 목록
```

---

## 5. 핵심 의존성 흐름

> **중요**: RAGFlow는 제거되었습니다. Milvus만 사용합니다.

```
┌──────────────────┐
│  FaqDraftService │
└────────┬─────────┘
         │
    ┌────┴────┬──────────────┐
    │         │              │
    ▼         ▼              ▼
┌────────┐ ┌────────────┐ ┌────────────┐
│PiiSvc  │ │MilvusSearch│ │ LLMClient  │
│        │ │   Client   │ │            │
└────┬───┘ └──────┬─────┘ └──────┬─────┘
     │            │              │
     ▼            ▼              ▼
┌────────────┐ ┌──────────────┐ ┌─────────────┐
│PII Service │ │   Milvus     │ │ LLM Service │
│ (GLiNER)   │ │ Vector DB    │ │/v1/chat/cmp │
│:8003/mask  │ │              │ │(Qwen2.5-7B) │
└────────────┘ └──────────────┘ └─────────────┘
```

### 파일 위치 및 역할

| 파일                           | 역할                        |
| ------------------------------ | --------------------------- |
| `app/api/v1/faq.py`            | API 엔드포인트 정의         |
| `app/models/faq.py`            | 요청/응답 Pydantic 모델     |
| `app/services/faq_service.py`  | 핵심 비즈니스 로직          |
| `app/services/pii_service.py`  | PII 검출/마스킹 서비스      |
| `app/clients/milvus_client.py` | Milvus 벡터 검색 클라이언트 |
| `app/clients/llm_client.py`    | LLM 호출 클라이언트         |

---

## 6. 상세 플로우

### 4.1 Step 1: 입력 PII 검사 (`_check_input_pii`)

**코드 위치**: `faq_service.py:649-694`

```python
# 검사 대상:
# - canonical_question
# - sample_questions (모든 항목)
# - top_docs.snippet (모든 항목)

await self._pii_service.detect_and_mask(text, MaskingStage.INPUT)
# PII 검출 시 → FaqGenerationError("PII_DETECTED")
```

### 4.2 Step 2: 문서 컨텍스트 확보 (`_get_context_docs`)

**코드 위치**: `faq_service.py:262-292`

```python
# 우선순위:
# 1. request.top_docs가 있으면 그대로 사용 (answer_source = "TOP_DOCS")
# 2. 없으면 Milvus 검색 (answer_source = "MILVUS")

if req.top_docs:
    return req.top_docs, "TOP_DOCS"

# Milvus 검색
return await self._search_milvus(req)
```

### 4.3 Step 2-1: Milvus 검색 (`_search_milvus`)

**코드 위치**: `faq_service.py:294-349`

```python
# Milvus 벡터 검색 (text 포함)
results = await self._milvus_client.search(
    query=req.canonical_question,
    domain=req.domain,
    top_k=5,
)
# 결과 없으면 → FaqGenerationError("NO_DOCS_FOUND")
```

### 4.4 Step 3: 컨텍스트 PII 검사 (`_check_context_pii`)

**코드 위치**: `faq_service.py:742-790`

```python
# Milvus 검색 결과의 snippet에서 PII 검사
# PII 발견 시 → FaqGenerationError("PII_DETECTED_CONTEXT")
```

### 4.5 Step 4: LLM 메시지 구성 (`_build_llm_messages`)

**코드 위치**: `faq_service.py:355-394`

```
┌─────────────────────────────────────────────────────────────────────────┐
│  SYSTEM_PROMPT (~800자)                                                 │
│  "너는 기업 내부 FAQ 작성 보조자다..."                                   │
│  - 컨텍스트 범위에서만 답변                                             │
│  - 추측 금지                                                            │
│  - LOW_RELEVANCE 시 status="LOW_RELEVANCE" 설정                         │
│  - 출력 형식: status, question, summary, answer_markdown, ai_confidence │
└─────────────────────────────────────────────────────────────────────────┘
                                  +
┌─────────────────────────────────────────────────────────────────────────┐
│  USER_PROMPT_TEMPLATE (~3200자)                                         │
│  ## 도메인: {domain}                                                    │
│  ## 대표 질문: {canonical_question}                                     │
│  ## 실제 직원 질문 예시: {sample_questions_text}                        │
│  ## 컨텍스트 문서: {docs_text}                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.6 Step 5: LLM 호출 (`LLMClient.generate_chat_completion`)

**코드 위치**: `faq_service.py:204-215`

```python
llm_response = await self._llm.generate_chat_completion(
    messages=messages,
    model=None,  # 설정에서 LLM_MODEL_NAME 사용 (Qwen2.5-7B)
    temperature=0.3,
    max_tokens=2048,
)
```

### 4.7 Step 6: 응답 파싱 (`_parse_llm_response`)

**코드 위치**: `faq_service.py:507-538`

```
LLM 응답 형식 (필드별 텍스트):
┌────────────────────────────────────────────┐
│ status: SUCCESS 또는 LOW_RELEVANCE         │
│ question: [다듬어진 FAQ 질문]              │
│ summary: [1문장, 120자 이내]               │
│ answer_markdown: |                         │
│   [결론 1~2문장]                           │
│   - [bullet 1]                             │
│   - [bullet 2]                             │
│   **참고**                                 │
│   - [문서 타이틀 (p.페이지)]               │
│ ai_confidence: 0.85                        │
└────────────────────────────────────────────┘
```

### 4.8 Step 7: 출력 PII 검사 (`_check_output_pii`)

**코드 위치**: `faq_service.py:696-736`

```python
# 검사 대상: answer_markdown, summary
# PII 발견 시 → FaqGenerationError("PII_DETECTED_OUTPUT")
```

### 4.9 Step 8: LOW_RELEVANCE 체크

**코드 위치**: `faq_service.py:228-243`

```python
if status == "LOW_RELEVANCE":
    if settings.FAQ_LOW_RELEVANCE_BLOCK:
        raise FaqGenerationError("LOW_RELEVANCE_CONTEXT")
    else:
        # 경고만 출력하고 계속 진행
        parsed["status"] = "SUCCESS"
```

### 4.10 Step 9-10: FaqDraft 생성 & 품질 로그

**코드 위치**: `faq_service.py:442-501`, `faq_service.py:796-850`

```python
# answer_source 결정: "TOP_DOCS" 또는 "MILVUS"
# ai_confidence 정규화 (0.0 ~ 1.0)
# 품질 모니터링: ai_confidence < threshold → WARN 로그
```

---

## 7. 단계별 데이터 변환 상세

### 5.1 Stage 0: API 요청 수신

#### 입력 (HTTP Request Body)

```json
// POST /ai/faq/generate
// Content-Type: application/json
// 크기: ~500B - 2KB

{
  "domain": "SEC_POLICY",
  "cluster_id": "cluster-2024-001",
  "canonical_question": "USB 메모리 반출 시 어떤 절차가 필요한가요?",
  "sample_questions": [
    "USB 반출하려면 어떻게 해요?",
    "외부 저장장치 가져가도 되나요?",
    "USB 승인 절차 알려주세요"
  ],
  "top_docs": [
    {
      "doc_id": "DOC-SEC-001",
      "doc_version": "v2.1",
      "title": "정보보안 관리규정",
      "snippet": "제3장 제2조 (외부저장매체 관리) ① 외부저장매체를 반출하고자 하는 경우...",
      "article_label": "제3장 제2조",
      "article_path": "제3장 > 제2조"
    }
  ]
}
```

### 5.2 Stage 2: 문서 컨텍스트 확보 (Milvus 검색)

> **Note**: RAGFlow는 더 이상 사용되지 않습니다.

#### top_docs가 없는 경우 → Milvus 검색

```
┌────────────────────────────────────────────────────────────────────────┐
│ Milvus 검색 요청                                                        │
│ MilvusSearchClient.search(query, domain, top_k=5)                       │
├────────────────────────────────────────────────────────────────────────┤
│ 내부적으로:                                                             │
│ 1. query를 임베딩 벡터로 변환 (ko-sroberta-multitask)                   │
│ 2. Milvus에서 유사도 검색                                               │
│ 3. 결과와 함께 text 필드 반환                                           │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Milvus 검색 결과                                                        │
│ ┌──────────────────────────────────────────────────────────────────┐   │
│ │ [                                                                │   │
│ │   {                                                              │   │
│ │     "doc_id": "정보보안_관리규정",                               │   │
│ │     "content": "제3장 제2조 (외부저장매체 관리)...",             │   │
│ │     "score": 0.92,                                               │   │
│ │     "metadata": {"chunk_id": 15}                                 │   │
│ │   },                                                             │   │
│ │   // ... 4개 더 (총 5개)                                         │   │
│ │ ]                                                                │   │
│ └──────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ RagSearchResult로 변환                                                  │
│ ┌──────────────────────────────────────────────────────────────────┐   │
│ │ [                                                                │   │
│ │   RagSearchResult(                                               │   │
│ │     title="정보보안_관리규정",                                   │   │
│ │     page=15,  # chunk_id                                         │   │
│ │     score=0.92,                                                  │   │
│ │     snippet="제3장 제2조 (외부저장..."  # 최대 500자             │   │
│ │   ),                                                             │   │
│ │   // ... 4개 더                                                  │   │
│ │ ]                                                                │   │
│ └──────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

### 5.3 Stage 5: LLM 호출

#### HTTP 요청

```
┌─────────────────────────────────────────────────────────────────────────┐
│ LLM HTTP Request                                                         │
│ POST {LLM_BASE_URL}/v1/chat/completions                                  │
├─────────────────────────────────────────────────────────────────────────┤
│ {                                                                        │
│   "model": "LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct",                                   │
│   "messages": [                                                          │
│     {                                                                   │
│       "role": "system",                                                 │
│       "content": "너는 기업 내부 FAQ 작성 보조자다.\n\n## 핵심 원칙..." │
│     },                                                    // ~800자     │
│     {                                                                   │
│       "role": "user",                                                   │
│       "content": "## 도메인\nSEC_POLICY\n\n## 대표 질문..."            │
│     }                                                     // ~3200자    │
│   ],                                                                    │
│   "temperature": 0.3,                                                   │
│   "max_tokens": 2048                                                    │
│ }                                                                        │
│                                                                         │
│ 요청 크기: ~8-9KB                                                        │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.4 최종 API 응답

```json
// HTTP Response
// Status: 200 OK
// Content-Type: application/json

{
  "status": "SUCCESS",
  "faq_draft": {
    "faq_draft_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "domain": "SEC_POLICY",
    "cluster_id": "cluster-2024-001",
    "question": "USB 메모리를 반출할 때 어떤 절차가 필요한가요?",
    "answer_markdown": "**정보보호팀의 사전 승인이 필요합니다.**\n\n- 반출 전 정보보호팀에 승인 요청\n- 반출 신청서 작성...",
    "summary": "정보보호팀의 사전 승인을 받고 반출 신청서를 제출해야 합니다.",
    "source_doc_id": null,
    "source_doc_version": null,
    "source_article_label": null,
    "source_article_path": null,
    "answer_source": "MILVUS",
    "ai_confidence": 0.91,
    "created_at": "2025-12-31T10:30:45Z"
  },
  "error_message": null
}
```

---

## 8. 배치 처리

### 6.1 배치 엔드포인트

**코드 위치**: `faq.py:242-338`

```
POST /ai/faq/generate/batch
```

### 6.2 배치 처리 흐름

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 배치 처리 (POST /ai/faq/generate/batch) 데이터 흐름                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  [입력] FaqDraftGenerateBatchRequest                                        │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ {                                                                  │    │
│  │   "items": [                                                       │    │
│  │     { /* FaqDraftGenerateRequest 1 */ },                           │    │
│  │     { /* FaqDraftGenerateRequest 2 */ },                           │    │
│  │     ...                                                            │    │
│  │   ],                                                               │    │
│  │   "concurrency": 3                                                 │    │
│  │ }                                                                  │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│              │                                                              │
│              ▼                                                              │
│  [병렬 처리] asyncio.Semaphore(concurrency=3)                               │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │                                                                    │    │
│  │    ┌─────────┐   ┌─────────┐   ┌─────────┐                        │    │
│  │    │ Item 1  │   │ Item 2  │   │ Item 3  │  ← 동시 실행 (3개)     │    │
│  │    │generate │   │generate │   │generate │                        │    │
│  │    │_faq_    │   │_faq_    │   │_faq_    │                        │    │
│  │    │draft()  │   │draft()  │   │draft()  │                        │    │
│  │    └────┬────┘   └────┬────┘   └────┬────┘                        │    │
│  │         │             │             │                              │    │
│  │         ▼             ▼             ▼                              │    │
│  │    ┌─────────┐   ┌─────────┐   ┌─────────┐                        │    │
│  │    │Response │   │Response │   │Response │                        │    │
│  │    │   1     │   │   2     │   │   3     │                        │    │
│  │    └─────────┘   └─────────┘   └─────────┘                        │    │
│  │                                                                    │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│              │                                                              │
│              ▼                                                              │
│  [출력] FaqDraftGenerateBatchResponse                                       │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ {                                                                  │    │
│  │   "items": [...],                                                  │    │
│  │   "total_count": 10,                                               │    │
│  │   "success_count": 8,                                              │    │
│  │   "failed_count": 2                                                │    │
│  │ }                                                                  │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 9. 에러 케이스

### 7.1 에러 코드 정리

| 에러 코드               | 발생 Stage | 원인                                     |
| ----------------------- | ---------- | ---------------------------------------- |
| `PII_DETECTED`          | Stage 1    | 입력에 PII                               |
| `NO_DOCS_FOUND`         | Stage 2    | Milvus 검색 결과 없음                    |
| `PII_DETECTED_CONTEXT`  | Stage 3    | 검색 결과 snippet에 PII                  |
| `LOW_RELEVANCE_CONTEXT` | Stage 8    | LLM이 컨텍스트가 질문과 관련 없다고 판단 |
| `PII_DETECTED_OUTPUT`   | Stage 7    | LLM 출력에 PII                           |

### 7.2 에러 응답 형식

```json
{
  "status": "FAILED",
  "faq_draft": null,
  "error_message": "PII_DETECTED"
}
```

---

## 10. 외부 서비스 연동

### 8.1 서비스 목록

> **Note**: RAGFlow는 제거되었습니다.

| 서비스          | 클라이언트           | 엔드포인트                                | 역할                  |
| --------------- | -------------------- | ----------------------------------------- | --------------------- |
| **PII Service** | `PiiService`         | `POST {PII_BASE_URL}/mask`                | 개인정보 검출/마스킹  |
| **Milvus**      | `MilvusSearchClient` | Milvus gRPC/HTTP                          | 벡터 검색             |
| **LLM**         | `LLMClient`          | `POST {LLM_BASE_URL}/v1/chat/completions` | FAQ 생성 (Qwen2.5-7B) |

### 8.2 외부 서비스 호출 횟수

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      외부 서비스 호출 횟수 (단건 기준)                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  서비스             Stage        호출 횟수          데이터 크기/건          │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                             │
│  PII Service        Stage 1      5회                ~100-600B              │
│  (입력 검사)                     (canonical + samples + snippets)           │
│                                                                             │
│  Milvus             Stage 2      0~1회              요청 ~150B             │
│  (문서 검색)                     (top_docs 없을 때만)  응답 ~3-5KB          │
│                                                                             │
│  PII Service        Stage 3      5회                ~500B/건               │
│  (컨텍스트 검사)                 (Milvus 결과 snippet별)                    │
│                                                                             │
│  LLM Service        Stage 5      1회                요청 ~8-9KB            │
│  (FAQ 생성)                      (Qwen2.5-7B)       응답 ~2-3KB            │
│                                                                             │
│  PII Service        Stage 7      2회                ~200-450B/건           │
│  (출력 검사)                     (answer_markdown + summary)               │
│                                                                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                             │
│  총 호출 횟수:                                                              │
│  - PII Service: 12회 (5 + 5 + 2)                                           │
│  - Milvus: 0~1회                                                           │
│  - LLM: 1회                                                                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 11. 캐시 및 설정

### 9.1 설정 값

```python
# app/core/config.py
FAQ_BATCH_CONCURRENCY = 3              # 배치 동시 처리 수
FAQ_CONFIDENCE_WARN_THRESHOLD = 0.7    # 품질 경고 임계값
FAQ_LOW_RELEVANCE_BLOCK = True         # LOW_RELEVANCE 차단 여부
FAQ_INTENT_CONFIDENCE_THRESHOLD = 0.7  # 의도 신뢰도 임계값
FAQ_INTENT_CONFIDENCE_REQUIRED = False # 의도 신뢰도 필수 여부
```

### 9.2 환경 변수

```bash
# PII 서비스
PII_BASE_URL=http://pii-service:8003
PII_ENABLED=true

# Milvus
MILVUS_HOST=milvus-server
MILVUS_PORT=19530

# LLM
LLM_BASE_URL=http://llm-server:8000/v1
LLM_MODEL_NAME=LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct
```

---

## 12. 데이터 크기 변화 요약

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        데이터 크기 변화 흐름                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Stage      데이터 형태                              크기                   │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                             │
│    0   ───→ HTTP Request (JSON)                      ~1-2KB                 │
│                      │                                                      │
│                      ▼                                                      │
│    1   ───→ texts_to_check (List[str])               ~0.7KB                 │
│                      │                                                      │
│                      ▼                                                      │
│    2   ───→ Milvus Search (if no top_docs)           ~3-5KB ◀── 가장 큼    │
│                      │                                                      │
│                      ▼                                                      │
│    4   ───→ LLM messages 배열                        ~4KB                   │
│                      │                                                      │
│                      ▼                                                      │
│    5   ───→ LLM Request (JSON)                       ~8-9KB ◀── 최대       │
│                      │                                                      │
│                      ▼                                                      │
│         ───→ LLM Response                            ~2-3KB                 │
│                      │                                                      │
│                      ▼                                                      │
│    6   ───→ parsed (dict)                            ~0.5KB                 │
│                      │                                                      │
│                      ▼                                                      │
│    9   ───→ FaqDraft 객체                            ~0.6KB                 │
│                      │                                                      │
│                      ▼                                                      │
│   11   ───→ HTTP Response (JSON)                     ~1.5-2KB               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 부록: 주요 코드 위치 참조

| 기능                  | 파일                           | 라인    |
| --------------------- | ------------------------------ | ------- |
| API 엔드포인트 (단건) | `app/api/v1/faq.py`            | 44-169  |
| API 엔드포인트 (배치) | `app/api/v1/faq.py`            | 242-338 |
| 데이터 모델           | `app/models/faq.py`            | 전체    |
| 메인 서비스 로직      | `app/services/faq_service.py`  | 176-256 |
| 입력 PII 검사         | `app/services/faq_service.py`  | 649-694 |
| Milvus 검색           | `app/services/faq_service.py`  | 294-349 |
| 컨텍스트 PII 검사     | `app/services/faq_service.py`  | 742-790 |
| LLM 메시지 구성       | `app/services/faq_service.py`  | 355-394 |
| 응답 파싱             | `app/services/faq_service.py`  | 507-619 |
| 출력 PII 검사         | `app/services/faq_service.py`  | 696-736 |
| FaqDraft 생성         | `app/services/faq_service.py`  | 442-501 |
| 품질 로그             | `app/services/faq_service.py`  | 796-890 |
| PII 서비스            | `app/services/pii_service.py`  | 전체    |
| Milvus 클라이언트     | `app/clients/milvus_client.py` | 전체    |
| LLM 클라이언트        | `app/clients/llm_client.py`    | 전체    |

---

## 변경 이력

| 날짜       | 내용                                                                                                    |
| ---------- | ------------------------------------------------------------------------------------------------------- |
| 2026-01-02 | End-to-End 시스템 플로우 및 백엔드-AI 서버 역할 분담 섹션 추가                                          |
| 2025-12-31 | RAGFlow 제거 반영, Milvus 직접 검색으로 변경, API 경로 수정 (`/ai/faq/*`), LLM 모델명 수정 (Qwen2.5-7B) |
| 2024-12-23 | 초기 작성                                                                                               |

---

_이 문서는 실제 코드 분석을 기반으로 작성되었습니다._
