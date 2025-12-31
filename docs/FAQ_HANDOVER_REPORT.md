# FAQ 파트 인수인계 보고서

> **작성일**: 2025-12-29
> **대상**: FAQ 파트 신규 담당자
> **프로젝트**: ctrlf-ai

---

## ⚠️ DEPRECATED NOTICE

> **이 문서는 더 이상 최신 상태가 아닙니다.**
>
> Phase 48 이후 RAGFlow가 제거되고 Milvus 전용으로 변경되었습니다.
> 최신 FAQ 파이프라인 정보는 다음 문서를 참조하세요:
>
> - **[FAQ_PIPELINE_ANALYSIS.md](./FAQ_PIPELINE_ANALYSIS.md)** - 최신 FAQ 파이프라인 분석 (Milvus 전용)
> - **[RETRIEVAL_FALLBACK_POLICY.md](./RETRIEVAL_FALLBACK_POLICY.md)** - 검색 정책 (RAGFlow 제거됨)
>
> 이 문서는 과거 기록용으로만 보관됩니다.

---

---

## 목차

1. [개요](#1-개요)
2. [파일 구조](#2-파일-구조)
3. [API 엔드포인트 상세](#3-api-엔드포인트-상세)
4. [데이터 모델 (Request/Response)](#4-데이터-모델-requestresponse)
5. [서비스 로직 상세](#5-서비스-로직-상세)
6. [의존성 서비스](#6-의존성-서비스)
7. [환경변수 설정](#7-환경변수-설정)
8. [에러 코드 및 처리](#8-에러-코드-및-처리)
9. [테스트 파일](#9-테스트-파일)
10. [백엔드 연동 가이드](#10-백엔드-연동-가이드)
11. [주의사항 및 개선 포인트](#11-주의사항-및-개선-포인트)

---

## 1. 개요

### 1.1 FAQ 초안 생성 기능이란?

FAQ 초안 생성 기능은 백엔드에서 전달받은 FAQ 후보 클러스터 정보를 기반으로, **RAG(Retrieval-Augmented Generation) + LLM**을 사용하여 FAQ 초안을 자동 생성하는 기능입니다.

### 1.2 주요 기능

| 기능          | 설명                                            | Phase                        |
| ------------- | ----------------------------------------------- | ---------------------------- |
| 단건 FAQ 생성 | 하나의 FAQ 클러스터에 대한 초안 생성            | Phase 18                     |
| 배치 FAQ 생성 | 다수의 FAQ 클러스터를 동시에 생성 (동시성 제한) | Phase 20-AI-2                |
| PII 강차단    | 입력/출력/컨텍스트에서 PII 검출 시 즉시 실패    | Phase 19-AI-4, Phase 20-AI-3 |
| RAGFlow 연동  | top_docs 없을 시 RAGFlow 검색으로 문서 확보     | Phase 19-AI-2                |
| 품질 모니터링 | ai_confidence 기반 경고 로그                    | Phase 20-AI-4                |
| RAG 캐시      | RAGFlow 검색 결과 캐싱 (TTL 300초)              | Phase 20-AI-1                |

### 1.3 전체 아키텍처 요약

```
┌─────────────────┐
│  Backend(Spring)│
│  FAQ 클러스터    │
└────────┬────────┘
         │ POST /ai/faq/generate
         ▼
┌─────────────────────────────────────────────────────────┐
│                   FastAPI (ctrlf-ai)                     │
│  ┌─────────────────┐    ┌──────────────────────────────┐│
│  │ app/api/v1/faq.py│───▶│ app/services/faq_service.py ││
│  │ (엔드포인트)      │    │ (FaqDraftService)           ││
│  └─────────────────┘    └────────────┬─────────────────┘│
│                                      │                   │
│  ┌──────────────────────────────────┬┴─────────────────┐│
│  │              │                   │                  ││
│  ▼              ▼                   ▼                  ││
│┌──────────┐ ┌───────────────┐ ┌───────────────────────┐││
││PiiService│ │RagflowSearch  │ │     LLMClient         │││
││(PII 검사)│ │Client         │ │  (FAQ 생성 LLM 호출)  │││
│└────┬─────┘ └───────┬───────┘ └───────────┬───────────┘││
│     │               │                     │            ││
└─────┼───────────────┼─────────────────────┼────────────┘│
      ▼               ▼                     ▼
┌──────────┐    ┌──────────┐         ┌──────────┐
│PII Service│   │ RAGFlow  │         │LLM Service│
│(GLiNER)  │   │ (문서검색)│         │ (vLLM)   │
│:8003/mask│   │/v1/chunk/ │         │/v1/chat/ │
│          │   │search     │         │completions│
└──────────┘   └──────────┘         └──────────┘
```

---

## 2. 파일 구조

### 2.1 핵심 파일 목록

```
ctrlf-ai/
├── app/
│   ├── api/v1/
│   │   └── faq.py                          # API 엔드포인트 정의
│   ├── models/
│   │   ├── faq.py                          # FAQ 요청/응답 Pydantic 모델
│   │   └── intent.py                       # PII 관련 모델 (MaskingStage, PiiMaskResult)
│   ├── services/
│   │   ├── faq_service.py                  # FAQ 생성 핵심 비즈니스 로직
│   │   └── pii_service.py                  # PII 마스킹 서비스
│   ├── clients/
│   │   ├── ragflow_search_client.py        # RAGFlow 검색 클라이언트
│   │   └── llm_client.py                   # LLM 호출 클라이언트
│   ├── core/
│   │   └── config.py                       # 환경변수 설정 (FAQ 관련 설정 포함)
│   ├── main.py                             # 라우터 등록
│   └── utils/
│       └── cache.py                        # TTLCache 유틸리티
├── tests/unit/
│   ├── test_faq_api_phase19.py             # Phase 19 API 통합 테스트
│   ├── test_faq_service_phase19.py         # Phase 19 서비스 단위 테스트
│   ├── test_faq_service_phase19_ai3.py     # Phase 19-AI-3 테스트
│   ├── test_faq_service_phase19_ai4.py     # Phase 19-AI-4 PII 테스트
│   ├── test_faq_batch_phase20.py           # Phase 20 배치 테스트
│   ├── test_faq_cache_phase20.py           # Phase 20 캐시 테스트
│   ├── test_faq_context_pii_phase20.py     # Phase 20 컨텍스트 PII 테스트
│   └── test_phase18_faq_generate.py        # Phase 18 기본 테스트
└── docs/
    ├── FAQ_PIPELINE_ANALYSIS.md            # 기존 파이프라인 분석 문서
    └── FAQ_HANDOVER_REPORT.md              # 이 문서
```

### 2.2 파일별 역할 상세

| 파일                                   | 라인 수 | 역할                                     |
| -------------------------------------- | ------- | ---------------------------------------- |
| `app/api/v1/faq.py`                    | 284줄   | API 엔드포인트 (단건/배치), 에러 핸들링  |
| `app/models/faq.py`                    | 149줄   | Request/Response DTO, FaqDraft 모델      |
| `app/services/faq_service.py`          | 873줄   | 핵심 비즈니스 로직, PII 검사, LLM 호출   |
| `app/services/pii_service.py`          | 328줄   | PII 마스킹 서비스 (GLiNER 연동)          |
| `app/clients/ragflow_search_client.py` | 378줄   | RAGFlow /v1/chunk/search 클라이언트      |
| `app/clients/llm_client.py`            | 424줄   | LLM /v1/chat/completions 클라이언트      |
| `app/models/intent.py`                 | 203줄   | MaskingStage, PiiMaskResult, PiiTag 모델 |

---

## 3. API 엔드포인트 상세

### 3.1 단건 FAQ 생성

```
POST /ai/faq/generate
```

**파일 위치**: `app/api/v1/faq.py:44-141`

#### Request Body

```json
{
  "domain": "SEC_POLICY",
  "cluster_id": "cluster-001",
  "canonical_question": "USB 메모리 반출 시 어떤 절차가 필요한가요?",
  "sample_questions": [
    "USB 반출하려면 어떻게 해요?",
    "외부 저장장치 가져가도 되나요?"
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

#### Request 필드 상세

| 필드                 | 타입                | 필수 | 설명                                                |
| -------------------- | ------------------- | ---- | --------------------------------------------------- |
| `domain`             | string              | O    | 도메인 (예: SEC_POLICY, PII_PRIVACY, TRAINING_QUIZ) |
| `cluster_id`         | string              | O    | FAQ 후보 클러스터 ID (백엔드에서 발급)              |
| `canonical_question` | string              | O    | 클러스터를 대표하는 질문                            |
| `sample_questions`   | array[string]       | X    | 실제 직원 질문 예시들 (최대 5개 사용)               |
| `top_docs`           | array[FaqSourceDoc] | X    | 백엔드가 이미 RAG에서 뽑아온 후보 문서들            |

#### FaqSourceDoc 필드

| 필드            | 타입   | 필수 | 설명                                    |
| --------------- | ------ | ---- | --------------------------------------- |
| `doc_id`        | string | O    | 문서 ID                                 |
| `doc_version`   | string | X    | 문서 버전                               |
| `title`         | string | X    | 문서 제목                               |
| `snippet`       | string | X    | 문서 발췌 내용 (최대 500자 권장)        |
| `article_label` | string | X    | 조항 라벨 (예: '제3장 제2조 제1항')     |
| `article_path`  | string | X    | 조항 경로 (예: '제3장 > 제2조 > 제1항') |

#### Response (성공 시)

```json
{
  "status": "SUCCESS",
  "faq_draft": {
    "faq_draft_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "domain": "SEC_POLICY",
    "cluster_id": "cluster-001",
    "question": "USB 메모리를 반출할 때 어떤 절차가 필요한가요?",
    "answer_markdown": "**정보보호팀의 사전 승인이 필요합니다.**\n\n- 반출 전 정보보호팀에 승인 요청\n- 반출 신청서 작성 (목적, 기간, 데이터 목록 명시)\n- 승인 후 반출 기록대장에 서명\n- 반입 시에도 정보보호팀에 신고 필요\n\n**참고**\n- 정보보안 관리규정 (p.15-18)",
    "summary": "정보보호팀의 사전 승인을 받고 반출 신청서를 제출해야 합니다.",
    "source_doc_id": "DOC-SEC-001",
    "source_doc_version": "v2.1",
    "source_article_label": "제3장 제2조",
    "source_article_path": "제3장 > 제2조",
    "answer_source": "TOP_DOCS",
    "ai_confidence": 0.91,
    "created_at": "2025-12-29T10:30:45Z"
  },
  "error_message": null
}
```

#### Response (실패 시)

```json
{
  "status": "FAILED",
  "faq_draft": null,
  "error_message": "PII_DETECTED"
}
```

#### FaqDraft 필드 상세

| 필드                   | 타입     | 설명                                 |
| ---------------------- | -------- | ------------------------------------ |
| `faq_draft_id`         | string   | UUID 형식의 FAQ 초안 ID              |
| `domain`               | string   | 도메인                               |
| `cluster_id`           | string   | 클러스터 ID                          |
| `question`             | string   | LLM이 다듬은 최종 FAQ 질문           |
| `answer_markdown`      | string   | FAQ 답변 (마크다운 형식)             |
| `summary`              | string   | 한 줄 요약 (최대 120자)              |
| `source_doc_id`        | string   | 근거 문서 ID (RAGFLOW인 경우 null)   |
| `source_doc_version`   | string   | 근거 문서 버전                       |
| `source_article_label` | string   | 근거 조항 라벨                       |
| `source_article_path`  | string   | 근거 조항 경로                       |
| `answer_source`        | enum     | 답변 출처: "TOP_DOCS" 또는 "RAGFLOW" |
| `ai_confidence`        | float    | AI 신뢰도 (0.0 ~ 1.0)                |
| `created_at`           | datetime | 생성 시각 (UTC)                      |

---

### 3.2 배치 FAQ 생성

```
POST /ai/faq/generate/batch
```

**파일 위치**: `app/api/v1/faq.py:187-283`

#### Request Body

```json
{
  "items": [
    {
      "domain": "SEC_POLICY",
      "cluster_id": "cluster-001",
      "canonical_question": "USB 반출 절차는?"
    },
    {
      "domain": "SEC_POLICY",
      "cluster_id": "cluster-002",
      "canonical_question": "비밀번호 변경 주기는?"
    }
  ],
  "concurrency": 3
}
```

| 필드          | 타입                           | 필수 | 설명                                                         |
| ------------- | ------------------------------ | ---- | ------------------------------------------------------------ |
| `items`       | array[FaqDraftGenerateRequest] | O    | FAQ 생성 요청 리스트 (최소 1개)                              |
| `concurrency` | int                            | X    | 동시 처리 수 (1-10, 기본값: 서버 설정 FAQ_BATCH_CONCURRENCY) |

#### Response

```json
{
  "items": [
    {
      "status": "SUCCESS",
      "faq_draft": { ... },
      "error_message": null
    },
    {
      "status": "FAILED",
      "faq_draft": null,
      "error_message": "PII_DETECTED"
    }
  ],
  "total_count": 2,
  "success_count": 1,
  "failed_count": 1
}
```

**중요**: 배치 처리 시 한 항목의 실패가 다른 항목에 영향을 미치지 않습니다. 응답 순서는 요청 순서와 동일합니다.

---

## 4. 데이터 모델 (Request/Response)

### 4.1 모델 파일 위치

`app/models/faq.py`

### 4.2 모델 클래스 다이어그램

```
┌─────────────────────────────────┐
│      FaqSourceDoc               │
├─────────────────────────────────┤
│ doc_id: str                     │
│ doc_version: Optional[str]      │
│ title: Optional[str]            │
│ snippet: Optional[str]          │
│ article_label: Optional[str]    │
│ article_path: Optional[str]     │
└─────────────────────────────────┘
              │
              ▼ (List[])
┌─────────────────────────────────┐
│   FaqDraftGenerateRequest       │
├─────────────────────────────────┤
│ domain: str                     │
│ cluster_id: str                 │
│ canonical_question: str         │
│ sample_questions: List[str]     │
│ top_docs: List[FaqSourceDoc]    │
└─────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│         FaqDraft                │
├─────────────────────────────────┤
│ faq_draft_id: str               │
│ domain: str                     │
│ cluster_id: str                 │
│ question: str                   │
│ answer_markdown: str            │
│ summary: Optional[str]          │
│ source_doc_id: Optional[str]    │
│ source_doc_version: Optional[str]│
│ source_article_label: Optional[str]│
│ source_article_path: Optional[str]│
│ answer_source: Literal[...]     │
│ ai_confidence: Optional[float]  │
│ created_at: datetime            │
└─────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│   FaqDraftGenerateResponse      │
├─────────────────────────────────┤
│ status: Literal["SUCCESS","FAILED"]│
│ faq_draft: Optional[FaqDraft]   │
│ error_message: Optional[str]    │
└─────────────────────────────────┘
```

### 4.3 answer_source 값 정의

| 값          | 설명                                              |
| ----------- | ------------------------------------------------- |
| `TOP_DOCS`  | 백엔드에서 제공한 top_docs를 사용하여 생성        |
| `RAGFLOW`   | AI 서버에서 RAGFlow 검색으로 문서를 확보하여 생성 |
| `AI_RAG`    | (레거시) 내부 RAG 사용                            |
| `LOG_REUSE` | (레거시) 로그 재사용                              |
| `MIXED`     | (레거시) 혼합                                     |

---

## 5. 서비스 로직 상세

### 5.1 FaqDraftService 클래스

**파일 위치**: `app/services/faq_service.py:153-873`

### 5.2 generate_faq_draft() 메인 플로우

```
┌────────────────────────────────────────────────────────────────────────┐
│                    generate_faq_draft() 메인 플로우                     │
│                    파일: faq_service.py:176-245                        │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  [Step 0] 로그 기록                                                    │
│         │                                                              │
│         ▼                                                              │
│  [Step 1] 입력 PII 검사 (_check_input_pii)                             │
│         │                                                              │
│         ├── PII 발견 ──→ FaqGenerationError("PII_DETECTED")            │
│         │                                                              │
│         ▼                                                              │
│  [Step 2] 문서 컨텍스트 확보 (_get_context_docs)                        │
│         │                                                              │
│         ├── top_docs 있음 ──→ 그대로 사용, used_top_docs=True          │
│         │                                                              │
│         └── top_docs 없음 ──→ RAGFlow 검색                             │
│                   │                                                    │
│                   ├── 결과 없음 ──→ FaqGenerationError("NO_DOCS_FOUND")│
│                   │                                                    │
│                   ▼                                                    │
│  [Step 3] 컨텍스트 PII 검사 (_check_context_pii)                       │
│         │                                                              │
│         ├── PII 발견 ──→ FaqGenerationError("PII_DETECTED_CONTEXT")   │
│         │                                                              │
│         ▼                                                              │
│  [Step 4] LLM 메시지 구성 (_build_llm_messages)                        │
│         │                                                              │
│         ▼                                                              │
│  [Step 5] LLM 호출 (LLMClient.generate_chat_completion)                │
│         │                                                              │
│         ├── 호출 실패 ──→ FaqGenerationError("LLM 호출 실패: ...")     │
│         │                                                              │
│         ▼                                                              │
│  [Step 6] 응답 파싱 (_parse_llm_response)                              │
│         │                                                              │
│         ├── 파싱 실패 ──→ FaqGenerationError("LLM 응답 파싱 실패")     │
│         │                                                              │
│         ▼                                                              │
│  [Step 7] 출력 PII 검사 (_check_output_pii)                            │
│         │                                                              │
│         ├── PII 발견 ──→ FaqGenerationError("PII_DETECTED_OUTPUT")    │
│         │                                                              │
│         ▼                                                              │
│  [Step 8] LOW_RELEVANCE 체크                                           │
│         │                                                              │
│         ├── status=LOW_RELEVANCE ──→ FaqGenerationError("LOW_RELEVANCE_CONTEXT")│
│         │                                                              │
│         ▼                                                              │
│  [Step 9] FaqDraft 생성 (_create_faq_draft)                            │
│         │                                                              │
│         ▼                                                              │
│  [Step 10] 품질 모니터링 로그 (_log_quality_metrics)                    │
│         │                                                              │
│         ▼                                                              │
│       FaqDraft 반환                                                    │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

### 5.3 LLM 프롬프트 템플릿

**파일 위치**: `app/services/faq_service.py:94-141`

#### System Prompt

```
너는 기업 내부 FAQ 작성 보조자다.

## 핵심 원칙
1. 답변은 반드시 제공된 컨텍스트(context_docs) 범위에서만 작성한다.
2. 컨텍스트에 없는 내용은 추측하지 않는다.
3. 컨텍스트가 질문과 관련이 없다고 판단되면 status를 "LOW_RELEVANCE"로 설정한다.

## 출력 형식
아래 형식으로 정확히 출력하라. 각 필드는 레이블과 콜론으로 시작한다.

status: SUCCESS 또는 LOW_RELEVANCE
question: [canonical_question을 자연스러운 FAQ 질문으로 다듬되 의미 변경 금지]
summary: [1문장, 최대 120자]
answer_markdown: |
  [결론 1~2문장]

  - [핵심 규칙/절차 bullet 1]
  - [핵심 규칙/절차 bullet 2]
  - [핵심 규칙/절차 bullet 3]
  (3~6개 bullet)

  **참고**
  - [문서 타이틀 (p.페이지)]
ai_confidence: [0.00~1.00, 컨텍스트 적합도가 높을수록 높게]

## 주의사항
- summary는 반드시 120자 이내로 작성
- answer_markdown의 bullet은 3~6개
- ai_confidence는 컨텍스트와 질문의 연관성을 0.00~1.00으로 평가
- 컨텍스트가 질문과 관련 없으면: status: LOW_RELEVANCE, ai_confidence: 0.3 이하
```

#### User Prompt Template

```
## 도메인
{domain}

## 대표 질문 (canonical_question)
{canonical_question}

## 실제 직원 질문 예시 (sample_questions)
{sample_questions_text}

## 컨텍스트 문서 (context_docs)
{docs_text}

위 컨텍스트를 바탕으로 FAQ를 작성해 주세요. 컨텍스트에 없는 내용은 작성하지 마세요.
```

### 5.4 LLM 응답 파싱

**파일 위치**: `app/services/faq_service.py:464-576`

LLM 응답은 두 가지 형식을 지원합니다:

1. **필드별 텍스트 형식** (권장)

```
status: SUCCESS
question: USB 메모리를 반출할 때 어떤 절차가 필요한가요?
summary: 정보보호팀의 사전 승인을 받고 반출 신청서를 제출해야 합니다.
answer_markdown: |
  **정보보호팀의 사전 승인이 필요합니다.**

  - 반출 전 정보보호팀에 승인 요청
  ...
ai_confidence: 0.91
```

2. **JSON 형식** (하위 호환)

```json
{
  "status": "SUCCESS",
  "question": "...",
  "summary": "...",
  "answer_markdown": "...",
  "ai_confidence": 0.91
}
```

---

## 6. 의존성 서비스

### 6.1 PiiService (PII 마스킹 서비스)

**파일 위치**: `app/services/pii_service.py`

#### 역할

- 개인식별정보(PII) 검출 및 마스킹
- GLiNER-PII 기반 HTTP 서비스와 통신

#### 호출 시점 (FAQ 파이프라인 내)

| 단계          | 검사 대상                                              | 에러 코드            |
| ------------- | ------------------------------------------------------ | -------------------- |
| 입력 검사     | canonical_question, sample_questions, top_docs.snippet | PII_DETECTED         |
| 컨텍스트 검사 | RAGFlow 검색 결과 snippet                              | PII_DETECTED_CONTEXT |
| 출력 검사     | answer_markdown, summary                               | PII_DETECTED_OUTPUT  |

#### API 스펙

```
POST {PII_BASE_URL}/mask
Content-Type: application/json

Request:
{
  "text": "홍길동 010-1234-5678",
  "stage": "input" | "output" | "log"
}

Response:
{
  "original_text": "홍길동 010-1234-5678",
  "masked_text": "[PERSON] [PHONE]",
  "has_pii": true,
  "tags": [
    {"entity": "홍길동", "label": "PERSON", "start": 0, "end": 3},
    {"entity": "010-1234-5678", "label": "PHONE", "start": 4, "end": 17}
  ]
}
```

---

### 6.2 RagflowSearchClient (RAGFlow 검색 클라이언트)

**파일 위치**: `app/clients/ragflow_search_client.py`

#### 역할

- top_docs가 없을 때 RAGFlow에서 관련 문서 검색
- domain -> kb_id 매핑 처리
- 검색 결과 캐싱 (TTL 300초)

#### API 스펙

```
POST {RAGFLOW_BASE_URL}/v1/chunk/search
Content-Type: application/json
Authorization: Bearer {RAGFLOW_API_KEY}

Request:
{
  "query": "USB 메모리 반출 시 어떤 절차가 필요한가요?",
  "dataset": "kb_sec_policy_001",
  "top_k": 5
}

Response:
{
  "data": {
    "results": [
      {
        "id": "chunk-001",
        "document_name": "정보보안_관리규정.pdf",
        "page_num": 15,
        "content": "제3장 제2조 (외부저장매체 관리)...",
        "similarity": 0.92
      }
    ]
  }
}
```

#### Domain -> KB_ID 매핑

환경변수로 설정:

```
RAGFLOW_DATASET_MAPPING=policy:kb_policy_001,training:kb_training_001,incident:kb_incident_001
```

또는 개별 변수:

```
RAGFLOW_KB_ID_POLICY=kb_policy_001
RAGFLOW_KB_ID_TRAINING=kb_training_001
```

---

### 6.3 LLMClient (LLM 클라이언트)

**파일 위치**: `app/clients/llm_client.py`

#### 역할

- FAQ 생성을 위한 LLM 호출
- OpenAI 호환 API 사용

#### API 스펙

```
POST {LLM_BASE_URL}/v1/chat/completions
Content-Type: application/json

Request:
{
  "model": "meta-llama/Meta-Llama-3-8B-Instruct",
  "messages": [
    {"role": "system", "content": "너는 기업 내부 FAQ 작성 보조자다..."},
    {"role": "user", "content": "## 도메인\nSEC_POLICY\n\n## 대표 질문..."}
  ],
  "temperature": 0.3,
  "max_tokens": 2048
}

Response:
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "status: SUCCESS\nquestion: ..."
      }
    }
  ]
}
```

#### FAQ 생성 시 LLM 호출 파라미터

| 파라미터    | 값                         | 설명                                |
| ----------- | -------------------------- | ----------------------------------- |
| temperature | 0.3                        | 낮은 temperature로 일관성 있는 응답 |
| max_tokens  | 2048                       | 충분한 답변 길이 확보               |
| model       | 설정값 또는 LLM_MODEL_NAME | 사용할 모델                         |

---

## 7. 환경변수 설정

### 7.1 FAQ 전용 설정

**파일 위치**: `app/core/config.py:132-143`

| 환경변수                        | 기본값 | 설명                          |
| ------------------------------- | ------ | ----------------------------- |
| `FAQ_RAG_CACHE_ENABLED`         | `True` | RAGFlow 검색 결과 캐시 활성화 |
| `FAQ_RAG_CACHE_TTL_SECONDS`     | `300`  | 캐시 TTL (초)                 |
| `FAQ_RAG_CACHE_MAXSIZE`         | `2048` | 최대 캐시 항목 수             |
| `FAQ_BATCH_CONCURRENCY`         | `4`    | 배치 동시 처리 수             |
| `FAQ_CONFIDENCE_WARN_THRESHOLD` | `0.6`  | 품질 경고 임계값              |

### 7.2 PII 서비스 설정

| 환경변수       | 기본값 | 설명                                                |
| -------------- | ------ | --------------------------------------------------- |
| `PII_BASE_URL` | (필수) | PII 마스킹 서비스 URL (예: http://pii-service:8003) |
| `PII_ENABLED`  | `True` | PII 마스킹 활성화 여부                              |

### 7.3 RAGFlow 설정

| 환경변수                  | 기본값                     | 설명                 |
| ------------------------- | -------------------------- | -------------------- |
| `RAGFLOW_BASE_URL`        | (필수)                     | RAGFlow 서비스 URL   |
| `RAGFLOW_API_KEY`         | (선택)                     | RAGFlow API Key      |
| `RAGFLOW_TIMEOUT_SEC`     | `10.0`                     | HTTP 요청 타임아웃   |
| `RAGFLOW_DATASET_MAPPING` | `policy:kb_policy_001,...` | Domain -> KB_ID 매핑 |

### 7.4 LLM 설정

| 환경변수         | 기본값                                | 설명              |
| ---------------- | ------------------------------------- | ----------------- |
| `LLM_BASE_URL`   | (필수)                                | LLM 서비스 URL    |
| `LLM_MODEL_NAME` | `meta-llama/Meta-Llama-3-8B-Instruct` | 사용할 LLM 모델명 |

### 7.5 .env 파일 예시

```env
# PII 서비스
PII_BASE_URL=http://pii-service:8003
PII_ENABLED=true

# RAGFlow
RAGFLOW_BASE_URL=http://ragflow:8080
RAGFLOW_API_KEY=your-api-key
RAGFLOW_DATASET_MAPPING=policy:kb_policy_001,training:kb_training_001

# LLM
LLM_BASE_URL=http://llm-service:8001
LLM_MODEL_NAME=meta-llama/Meta-Llama-3-8B-Instruct

# FAQ 설정
FAQ_BATCH_CONCURRENCY=4
FAQ_CONFIDENCE_WARN_THRESHOLD=0.6
FAQ_RAG_CACHE_ENABLED=true
FAQ_RAG_CACHE_TTL_SECONDS=300
```

---

## 8. 에러 코드 및 처리

### 8.1 에러 코드 목록

| 에러 코드                | 발생 단계     | 원인                                                              | 대응 방안                             |
| ------------------------ | ------------- | ----------------------------------------------------------------- | ------------------------------------- |
| `PII_DETECTED`           | 입력 검사     | canonical_question, sample_questions, top_docs.snippet에 PII 포함 | 백엔드에서 PII 제거 후 재요청         |
| `PII_DETECTED_CONTEXT`   | 컨텍스트 검사 | RAGFlow 검색 결과 snippet에 PII 포함                              | RAGFlow 문서 정제 필요                |
| `PII_DETECTED_OUTPUT`    | 출력 검사     | LLM 응답(answer_markdown, summary)에 PII 포함                     | LLM 프롬프트 강화 또는 재시도         |
| `NO_DOCS_FOUND`          | 컨텍스트 확보 | RAGFlow 검색 결과가 0개                                           | domain 매핑 확인, RAGFlow 인덱싱 확인 |
| `LOW_RELEVANCE_CONTEXT`  | LLM 응답 파싱 | LLM이 컨텍스트가 질문과 무관하다고 판단                           | 더 적절한 문서 제공 필요              |
| `LLM 호출 실패: ...`     | LLM 호출      | LLM 서비스 장애 또는 타임아웃                                     | LLM 서비스 상태 확인                  |
| `LLM 응답 파싱 실패`     | 응답 파싱     | LLM 응답이 예상 형식과 다름                                       | LLM 프롬프트 조정 필요                |
| `RAGFlow 설정 오류: ...` | 컨텍스트 확보 | domain에 대한 kb_id 매핑 없음                                     | RAGFLOW_DATASET_MAPPING 설정 확인     |
| `RAGFlow 검색 실패: ...` | 컨텍스트 확보 | RAGFlow 서비스 장애                                               | RAGFlow 서비스 상태 확인              |

### 8.2 에러 응답 형식

모든 에러는 HTTP 200으로 반환되며, status="FAILED"로 표시됩니다:

```json
{
  "status": "FAILED",
  "faq_draft": null,
  "error_message": "PII_DETECTED"
}
```

### 8.3 에러 처리 코드 위치

**파일 위치**: `app/api/v1/faq.py:116-141`

```python
try:
    draft = await service.generate_faq_draft(request)
    return FaqDraftGenerateResponse(status="SUCCESS", faq_draft=draft)

except FaqGenerationError as e:
    logger.warning(f"FAQ generation failed: {e}")
    return FaqDraftGenerateResponse(
        status="FAILED",
        faq_draft=None,
        error_message=str(e),
    )

except Exception as e:
    logger.exception(f"Unexpected error in FAQ generation: {e}")
    return FaqDraftGenerateResponse(
        status="FAILED",
        faq_draft=None,
        error_message=f"예기치 않은 오류: {type(e).__name__}: {str(e)}",
    )
```

---

## 9. 테스트 파일

### 9.1 테스트 파일 목록

| 파일                              | 테스트 수     | 설명                          |
| --------------------------------- | ------------- | ----------------------------- |
| `test_phase18_faq_generate.py`    | 기본 테스트   | Phase 18 기본 FAQ 생성 테스트 |
| `test_faq_api_phase19.py`         | 12개          | Phase 19 API 통합 테스트      |
| `test_faq_service_phase19.py`     | 서비스 테스트 | Phase 19 서비스 단위 테스트   |
| `test_faq_service_phase19_ai3.py` | AI-3 테스트   | 프롬프트/파싱 테스트          |
| `test_faq_service_phase19_ai4.py` | AI-4 테스트   | PII 강차단 테스트             |
| `test_faq_batch_phase20.py`       | 4개           | 배치 처리 테스트              |
| `test_faq_cache_phase20.py`       | 캐시 테스트   | RAG 캐시 테스트               |
| `test_faq_context_pii_phase20.py` | PII 테스트    | 컨텍스트 PII 검사 테스트      |

### 9.2 테스트 실행 방법

```bash
# 전체 FAQ 테스트 실행
python -m pytest tests/unit/test_faq*.py -v

# 특정 Phase 테스트만 실행
python -m pytest tests/unit/test_faq_api_phase19.py -v

# 배치 테스트만 실행
python -m pytest tests/unit/test_faq_batch_phase20.py -v
```

### 9.3 주요 테스트 케이스

**test_faq_api_phase19.py 주요 테스트**:

1. `test_api_with_top_docs_success` - top_docs 제공 시 성공
2. `test_service_with_top_docs_no_ragflow_call` - top_docs 제공 시 RAGFlow 미호출 확인
3. `test_ragflow_search_called_and_answer_source_ragflow` - RAGFlow 검색 시 answer_source=RAGFLOW
4. `test_api_no_docs_found` - 검색 결과 없을 때 NO_DOCS_FOUND
5. `test_api_pii_detected_in_input` - 입력 PII 검출 시 PII_DETECTED
6. `test_api_pii_detected_in_output` - 출력 PII 검출 시 PII_DETECTED_OUTPUT
7. `test_api_ragflow_timeout_error` - RAGFlow 타임아웃 처리

---

## 10. 백엔드 연동 가이드

### 10.1 호출 시나리오

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           백엔드 호출 시나리오                            │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  시나리오 1: 백엔드가 이미 RAG 검색을 수행한 경우                         │
│  ─────────────────────────────────────────────────────────────────────   │
│  1. 백엔드에서 FAQ 클러스터 생성                                          │
│  2. 백엔드에서 RAG 검색으로 관련 문서(top_docs) 확보                      │
│  3. POST /ai/faq/generate 호출 (top_docs 포함)                           │
│  4. AI 서버: RAGFlow 호출 없이 바로 LLM으로 FAQ 생성                      │
│  5. answer_source = "TOP_DOCS"                                           │
│                                                                          │
│                                                                          │
│  시나리오 2: 백엔드가 RAG 검색을 하지 않은 경우                           │
│  ─────────────────────────────────────────────────────────────────────   │
│  1. 백엔드에서 FAQ 클러스터 생성                                          │
│  2. POST /ai/faq/generate 호출 (top_docs 없음)                           │
│  3. AI 서버: RAGFlow /v1/chunk/search 호출로 문서 검색                   │
│  4. AI 서버: 검색된 문서로 LLM FAQ 생성                                   │
│  5. answer_source = "RAGFLOW"                                            │
│                                                                          │
│                                                                          │
│  시나리오 3: 배치 처리                                                    │
│  ─────────────────────────────────────────────────────────────────────   │
│  1. 백엔드에서 다수의 FAQ 클러스터 수집                                   │
│  2. POST /ai/faq/generate/batch 호출 (items 배열)                        │
│  3. AI 서버: 동시성 제한(기본 4개)으로 병렬 처리                          │
│  4. 각 항목은 독립적으로 처리 (한 개 실패해도 다른 것에 영향 없음)         │
│  5. 응답 순서 = 요청 순서                                                 │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 10.2 권장 타임아웃

| 엔드포인트               | 권장 타임아웃 | 이유           |
| ------------------------ | ------------- | -------------- |
| `/ai/faq/generate`       | 60초          | LLM 호출 포함  |
| `/ai/faq/generate/batch` | 180초         | 다수 항목 처리 |

### 10.3 에러 핸들링 권장사항

```java
// Spring 백엔드 예시
FaqGenerateResponse response = aiClient.generateFaq(request);

if ("FAILED".equals(response.getStatus())) {
    String errorMessage = response.getErrorMessage();

    switch (errorMessage) {
        case "PII_DETECTED":
            // 입력 데이터에 개인정보 포함
            // -> 데이터 정제 후 재시도 또는 관리자 알림
            break;

        case "NO_DOCS_FOUND":
            // 관련 문서 없음
            // -> 해당 클러스터 스킵 또는 수동 처리 대상으로 표시
            break;

        case "LOW_RELEVANCE_CONTEXT":
            // 문서가 질문과 무관
            // -> 더 적절한 문서 필요, 수동 처리 대상
            break;

        default:
            // 기타 에러 (LLM 장애 등)
            // -> 재시도 또는 에러 로그
            break;
    }
}
```

### 10.4 배치 처리 권장사항

1. **배치 크기**: 한 번에 10-20개 권장 (너무 많으면 타임아웃 위험)
2. **동시성**: 기본 4개, 최대 10개까지 설정 가능
3. **에러 처리**: 각 항목의 status 개별 확인 필요
4. **순서 보장**: 응답 items의 순서는 요청 items 순서와 동일

---

## 11. 주의사항 및 개선 포인트

### 11.1 현재 알려진 제한사항

| 항목          | 설명                                   | 영향                  |
| ------------- | -------------------------------------- | --------------------- |
| PII Fallback  | PII 서비스 장애 시 원문 그대로 반환    | 민감정보 노출 가능성  |
| 캐시 무효화   | 문서 업데이트 시 캐시 수동 무효화 필요 | 최신 문서 미반영 가능 |
| LLM 응답 파싱 | LLM 출력 형식이 다를 경우 파싱 실패    | 재시도 필요           |
| Domain 매핑   | 새 도메인 추가 시 환경변수 설정 필요   | 설정 누락 시 에러     |

### 11.2 백엔드 수정 시 주의사항

1. **API 스펙 변경 시**:

   - `app/models/faq.py`의 Pydantic 모델 수정
   - `docs/API_SPECIFICATION_FOR_BACKEND.md` 문서 업데이트
   - 관련 테스트 케이스 수정

2. **새 에러 코드 추가 시**:

   - `app/services/faq_service.py`에서 `FaqGenerationError` 발생
   - 백엔드에 에러 코드 문서 전달

3. **Domain 추가 시**:
   - `RAGFLOW_DATASET_MAPPING` 환경변수에 매핑 추가
   - 또는 `RAGFLOW_KB_ID_{DOMAIN}` 환경변수 추가

### 11.3 향후 개선 포인트

| 우선순위 | 개선 항목          | 설명                                          |
| -------- | ------------------ | --------------------------------------------- |
| 높음     | PII 강제 차단 모드 | PII 서비스 장애 시에도 에러 반환 옵션         |
| 중간     | 캐시 자동 무효화   | RAGFlow 문서 업데이트 시 캐시 무효화 훅       |
| 중간     | 재시도 로직 강화   | LLM 응답 파싱 실패 시 프롬프트 변경 후 재시도 |
| 낮음     | 스트리밍 응답      | 긴 FAQ 생성 시 진행률 표시                    |

### 11.4 디버깅 팁

1. **로그 확인**:

   ```bash
   # FAQ 관련 로그만 필터링
   grep "FAQ\|faq" logs/app.log
   ```

2. **PII 검사 바이패스 (개발 환경에서만)**:

   ```env
   PII_ENABLED=false
   ```

3. **캐시 비활성화 (디버깅용)**:

   ```env
   FAQ_RAG_CACHE_ENABLED=false
   ```

4. **LLM 응답 확인**:
   - `faq_service.py:212`에서 `logger.debug(f"LLM response: ...")` 로그 확인
   - LOG_LEVEL=DEBUG로 설정

---

## 부록: 코드 위치 참조 테이블

| 기능                   | 파일                                   | 라인    |
| ---------------------- | -------------------------------------- | ------- |
| API 엔드포인트 (단건)  | `app/api/v1/faq.py`                    | 44-141  |
| API 엔드포인트 (배치)  | `app/api/v1/faq.py`                    | 187-283 |
| 요청/응답 모델         | `app/models/faq.py`                    | 전체    |
| FaqDraftService 클래스 | `app/services/faq_service.py`          | 153-873 |
| generate_faq_draft()   | `app/services/faq_service.py`          | 176-245 |
| 입력 PII 검사          | `app/services/faq_service.py`          | 606-651 |
| 컨텍스트 확보          | `app/services/faq_service.py`          | 251-303 |
| 컨텍스트 PII 검사      | `app/services/faq_service.py`          | 699-747 |
| LLM 메시지 구성        | `app/services/faq_service.py`          | 309-348 |
| LLM 프롬프트 템플릿    | `app/services/faq_service.py`          | 94-141  |
| 응답 파싱              | `app/services/faq_service.py`          | 464-576 |
| 출력 PII 검사          | `app/services/faq_service.py`          | 653-692 |
| FaqDraft 생성          | `app/services/faq_service.py`          | 396-458 |
| 품질 로그              | `app/services/faq_service.py`          | 753-806 |
| PII 서비스             | `app/services/pii_service.py`          | 전체    |
| RAGFlow 클라이언트     | `app/clients/ragflow_search_client.py` | 전체    |
| LLM 클라이언트         | `app/clients/llm_client.py`            | 전체    |
| FAQ 설정               | `app/core/config.py`                   | 132-143 |
| 라우터 등록            | `app/main.py`                          | 161     |

---

_이 문서는 실제 코드 분석을 기반으로 작성되었습니다._
_최종 수정: 2025-12-29_
