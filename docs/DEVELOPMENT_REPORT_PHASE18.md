# Phase 18: FAQ 초안 생성 API 구현

## 개요

Phase 18에서는 **FAQ 초안 생성 API** (`POST /ai/faq/generate`)를 구현했습니다. 백엔드에서 FAQ 후보 클러스터 정보를 전송하면, AI Gateway가 RAG + LLM을 활용하여 FAQ 초안을 자동 생성합니다.

이를 통해 반복 문의에 대한 FAQ를 효율적으로 작성할 수 있으며, 관리자는 생성된 초안을 검토/수정 후 게시할 수 있습니다.

## 구현 내용

### 1. 전체 파이프라인

```
┌─────────────────────────────────────────────────────────────────┐
│                     FAQ 초안 생성 파이프라인                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ctrlf-back                      ctrlf-ai-gateway              │
│  ───────────                     ────────────────              │
│      │                                │                        │
│      │  POST /ai/faq/generate        │                        │
│      │  {domain, cluster_id,         │                        │
│      │   canonical_question,         │                        │
│      │   sample_questions,           │                        │
│      │   top_docs?, answer_hint?}    │                        │
│      │ ─────────────────────────────>│                        │
│      │                                │                        │
│      │                                │  1. 문서 확보           │
│      │                                │     (top_docs 또는 RAG) │
│      │                                │                        │
│      │                                │  2. LLM 프롬프트 구성    │
│      │                                │                        │
│      │                                │  3. FAQ 초안 생성       │
│      │                                │                        │
│      │  FaqDraftGenerateResponse     │                        │
│      │  {status, faq_draft, error?}  │                        │
│      │ <─────────────────────────────│                        │
│      │                                │                        │
└─────────────────────────────────────────────────────────────────┘
```

### 2. 파일 구조

```
app/
├── models/
│   └── faq.py                # FAQ DTO 모델 정의
├── services/
│   └── faq_service.py        # FaqDraftService 구현
└── api/v1/
    └── faq.py                # POST /ai/faq/generate 엔드포인트

tests/
└── test_phase18_faq_generate.py  # 22개 테스트
```

### 3. 요청/응답 모델

#### FaqDraftGenerateRequest (요청)

```python
class FaqDraftGenerateRequest(BaseModel):
    domain: str                           # 도메인 (POLICY, INCIDENT, EDUCATION)
    cluster_id: str                       # 클러스터 ID
    canonical_question: str               # 대표 질문
    sample_questions: List[str] = []      # 유사 질문 예시
    top_docs: List[FaqSourceDoc] = []     # 참조 문서 (선택)
    answer_source_hint: Optional[str]     # 답변 소스 힌트
    meta: Optional[Dict[str, Any]]        # 추가 메타 정보
```

#### FaqSourceDoc (참조 문서)

```python
class FaqSourceDoc(BaseModel):
    doc_id: str                           # 문서 ID
    doc_version: Optional[str]            # 문서 버전
    article_label: Optional[str]          # 조항 레이블 (예: "제5조")
    article_path: Optional[str]           # 조항 경로
    snippet: str                          # 문서 스니펫
```

#### FaqDraft (생성된 FAQ 초안)

```python
class FaqDraft(BaseModel):
    faq_draft_id: str                     # 초안 고유 ID
    domain: str                           # 도메인
    cluster_id: str                       # 클러스터 ID
    question: str                         # 정제된 질문
    answer_markdown: str                  # 답변 (마크다운)
    summary: Optional[str]                # 요약
    source_doc_id: Optional[str]          # 출처 문서 ID
    source_doc_version: Optional[str]     # 출처 문서 버전
    source_article_label: Optional[str]   # 출처 조항 레이블
    source_article_path: Optional[str]    # 출처 조항 경로
    answer_source: Literal["AI_RAG", "LOG_REUSE", "MIXED"]
    ai_confidence: Optional[float]        # AI 신뢰도 (0.0 ~ 1.0)
    created_at: datetime                  # 생성 시각
```

#### FaqDraftGenerateResponse (응답)

```python
class FaqDraftGenerateResponse(BaseModel):
    status: Literal["SUCCESS", "FAILED"]
    faq_draft: Optional[FaqDraft]
    error_message: Optional[str]
```

### 4. FaqDraftService 구현

```python
class FaqDraftService:
    """FAQ 초안 생성 서비스"""

    def __init__(
        self,
        rag_client: Optional[RagflowClient] = None,
        llm_client: Optional[LLMClient] = None,
    ):
        self._rag_client = rag_client or RagflowClient(...)
        self._llm = llm_client or LLMClient()

    async def generate_faq_draft(
        self,
        request: FaqDraftGenerateRequest
    ) -> FaqDraft:
        """
        FAQ 초안 생성 메인 로직

        1. 문서 확보 (top_docs 또는 RAG 검색)
        2. LLM 프롬프트 구성
        3. LLM 호출 및 응답 파싱
        4. FaqDraft 객체 생성 후 반환
        """
        # 1. 문서 확보
        source_docs = await self._get_source_docs(request)

        # 2. LLM 프롬프트 구성
        messages = self._build_llm_messages(request, source_docs)

        # 3. LLM 호출
        llm_response = await self._llm.generate_chat_completion(
            messages=messages,
            model="gpt-4o-mini",
            temperature=0.3,
            max_tokens=2000,
        )

        # 4. 응답 파싱 및 FaqDraft 생성
        parsed = self._parse_llm_response(llm_response)

        return FaqDraft(
            faq_draft_id=self._generate_draft_id(),
            domain=request.domain,
            cluster_id=request.cluster_id,
            question=parsed.get("question", request.canonical_question),
            answer_markdown=parsed.get("answer", ""),
            summary=parsed.get("summary"),
            source_doc_id=source_docs[0].doc_id if source_docs else None,
            # ... 기타 필드
        )
```

### 5. LLM 프롬프트 설계

#### System Prompt

```
당신은 기업 내부 FAQ를 작성하는 전문가입니다.
주어진 질문과 참조 문서를 바탕으로 명확하고 정확한 FAQ 답변을 작성해주세요.

작성 지침:
1. 참조 문서의 내용을 근거로 답변하세요
2. 불확실한 내용은 포함하지 마세요
3. 마크다운 형식을 사용하여 가독성을 높이세요
4. 답변은 간결하되 필요한 정보는 모두 포함하세요

반드시 아래 JSON 형식으로 응답하세요:
{
    "question": "정제된 질문",
    "answer": "마크다운 형식의 답변",
    "summary": "한 문장 요약 (선택)",
    "confidence": 0.0~1.0 사이의 신뢰도
}
```

#### User Prompt

```
## 도메인
{domain}

## 대표 질문
{canonical_question}

## 유사 질문 예시
- {sample_question_1}
- {sample_question_2}
...

## 참조 문서
### 문서 1: {doc_id}
{snippet}

### 문서 2: {doc_id}
{snippet}
...

## 답변 힌트 (선택)
{answer_source_hint}

위 정보를 바탕으로 FAQ 초안을 JSON 형식으로 작성해주세요.
```

### 6. 문서 확보 전략

```python
async def _get_source_docs(
    self,
    request: FaqDraftGenerateRequest
) -> List[FaqSourceDoc]:
    """
    문서 확보 전략:
    1. top_docs가 있으면 그대로 사용
    2. 없으면 RAG 검색으로 관련 문서 확보
    """
    if request.top_docs:
        return request.top_docs

    # RAG 검색
    rag_results = await self._rag_client.search(
        query=request.canonical_question,
        dataset=request.domain,
        top_k=3,
    )

    return [
        FaqSourceDoc(
            doc_id=doc.doc_id,
            snippet=doc.snippet,
            doc_version=None,
            article_label=None,
            article_path=None,
        )
        for doc in rag_results
    ]
```

### 7. API 엔드포인트

```python
router = APIRouter(prefix="/faq", tags=["FAQ"])

@router.post("/generate", response_model=FaqDraftGenerateResponse)
async def generate_faq_draft(
    request: FaqDraftGenerateRequest,
) -> FaqDraftGenerateResponse:
    """
    FAQ 초안 생성 API

    POST /ai/faq/generate

    Request:
        - domain: 도메인 (POLICY, INCIDENT, EDUCATION)
        - cluster_id: 클러스터 ID
        - canonical_question: 대표 질문
        - sample_questions: 유사 질문 목록 (선택)
        - top_docs: 참조 문서 목록 (선택)
        - answer_source_hint: 답변 힌트 (선택)
        - meta: 추가 메타 정보 (선택)

    Response:
        - status: SUCCESS / FAILED
        - faq_draft: 생성된 FAQ 초안 (성공 시)
        - error_message: 에러 메시지 (실패 시)
    """
    service = FaqDraftService()

    try:
        draft = await service.generate_faq_draft(request)
        return FaqDraftGenerateResponse(
            status="SUCCESS",
            faq_draft=draft,
        )
    except FaqGenerationError as e:
        logger.error(f"FAQ 생성 실패: {e}")
        return FaqDraftGenerateResponse(
            status="FAILED",
            error_message=str(e),
        )
    except Exception as e:
        logger.error(f"FAQ 생성 중 예상치 못한 오류: {e}")
        return FaqDraftGenerateResponse(
            status="FAILED",
            error_message="FAQ 생성 중 오류가 발생했습니다.",
        )
```

## 테스트 결과

### Phase 18 테스트 (22개 통과)

```
TestFaqModels: 6개 - FAQ 모델 검증
  - FaqSourceDoc 생성
  - FaqDraftGenerateRequest 필드 검증
  - FaqDraft 생성
  - FaqDraftGenerateResponse 성공/실패 케이스

TestFaqDraftService: 8개 - 서비스 로직 검증
  - top_docs 제공 시 RAG 스킵
  - top_docs 없으면 RAG 검색
  - LLM 프롬프트에 문서 포함 확인
  - JSON 파싱 성공/실패
  - RAG 빈 결과 처리
  - LLM 호출 실패 처리
  - AI confidence 파싱

TestFaqApi: 6개 - API 엔드포인트 검증
  - 성공 응답 검증
  - 실패 응답 검증
  - 필수 필드 누락 시 422 에러
  - 빈 문자열 필드 시 422 에러

TestFaqIntegration: 2개 - 통합 테스트
  - 전체 파이프라인 흐름 검증
  - 다양한 도메인 지원
```

### 전체 테스트

```
============================= 407 passed in 5.45s =============================
```

## 변경된 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/models/faq.py` | 신규 - FAQ DTO 모델 |
| `app/services/faq_service.py` | 신규 - FaqDraftService 구현 |
| `app/api/v1/faq.py` | 신규 - POST /ai/faq/generate 엔드포인트 |
| `app/api/v1/__init__.py` | 수정 - faq 모듈 export 추가 |
| `app/main.py` | 수정 - faq 라우터 등록 |
| `tests/test_phase18_faq_generate.py` | 신규 - 22개 테스트 |

## 사용 예시

### API 호출 (curl)

```bash
curl -X POST http://localhost:8000/ai/faq/generate \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "POLICY",
    "cluster_id": "cluster-001",
    "canonical_question": "연차휴가 이월 규정은 어떻게 되나요?",
    "sample_questions": [
      "연차 이월 가능한가요?",
      "작년 연차 올해 쓸 수 있나요?",
      "남은 연차 다음 해로 넘기기"
    ],
    "top_docs": [
      {
        "doc_id": "doc-policy-001",
        "doc_version": "v2.1",
        "article_label": "제15조",
        "article_path": "인사규정 > 휴가 > 연차휴가",
        "snippet": "연차휴가는 다음 연도에 한하여 최대 10일까지 이월할 수 있다..."
      }
    ],
    "answer_source_hint": "인사규정 제15조 참조"
  }'
```

### 응답 예시 (성공)

```json
{
  "status": "SUCCESS",
  "faq_draft": {
    "faq_draft_id": "faq-20251212-abc12345",
    "domain": "POLICY",
    "cluster_id": "cluster-001",
    "question": "연차휴가 이월 규정은 어떻게 되나요?",
    "answer_markdown": "## 연차휴가 이월 규정\n\n연차휴가는 **다음 연도에 한하여 최대 10일까지** 이월할 수 있습니다.\n\n### 주요 사항\n- 이월 가능 기간: 다음 연도까지\n- 최대 이월 일수: 10일\n\n📌 **참고**: 인사규정 제15조",
    "summary": "연차휴가는 다음 해까지 최대 10일 이월 가능",
    "source_doc_id": "doc-policy-001",
    "source_doc_version": "v2.1",
    "source_article_label": "제15조",
    "source_article_path": "인사규정 > 휴가 > 연차휴가",
    "answer_source": "AI_RAG",
    "ai_confidence": 0.92,
    "created_at": "2025-12-12T10:30:00Z"
  }
}
```

### 응답 예시 (실패)

```json
{
  "status": "FAILED",
  "faq_draft": null,
  "error_message": "LLM 응답 파싱 실패: JSON 형식이 아닙니다."
}
```

### Python SDK 사용

```python
from app.services.faq_service import FaqDraftService
from app.models.faq import FaqDraftGenerateRequest, FaqSourceDoc

service = FaqDraftService()

request = FaqDraftGenerateRequest(
    domain="INCIDENT",
    cluster_id="cluster-security-001",
    canonical_question="보안 사고 발생 시 어떻게 신고하나요?",
    sample_questions=[
        "보안 사고 신고 절차",
        "해킹 의심될 때 어디로 연락?",
    ],
)

draft = await service.generate_faq_draft(request)
print(f"질문: {draft.question}")
print(f"답변: {draft.answer_markdown}")
print(f"신뢰도: {draft.ai_confidence}")
```

## 백엔드 연동 가이드

### 호출 타이밍

1. **수동 생성**: 관리자가 FAQ 생성 버튼 클릭 시
2. **자동 생성**: 유사 질문 클러스터가 임계값 도달 시 (예: 동일 질문 5회 이상)

### 연동 흐름

```
┌────────────────────────────────────────────────────────────┐
│                    ctrlf-back (Spring)                      │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  1. 유사 질문 클러스터링                                      │
│     - 채팅 로그 분석                                         │
│     - 의미적 유사도 기반 그룹화                                │
│                                                            │
│  2. FAQ 후보 선정                                            │
│     - 빈도 기반 우선순위                                      │
│     - 미답변 질문 우선                                        │
│                                                            │
│  3. AI Gateway 호출                                         │
│     POST /ai/faq/generate                                   │
│     {domain, cluster_id, canonical_question, ...}           │
│                                                            │
│  4. 초안 저장                                                │
│     - faq_drafts 테이블에 저장                               │
│     - status = DRAFT                                        │
│                                                            │
│  5. 관리자 검토                                              │
│     - 초안 수정/승인/거부                                     │
│     - 승인 시 status = PUBLISHED                             │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 권장 테이블 스키마 (Spring 측)

```sql
CREATE TABLE faq_drafts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    faq_draft_id VARCHAR(50) UNIQUE NOT NULL,
    domain VARCHAR(50) NOT NULL,
    cluster_id VARCHAR(50) NOT NULL,
    question TEXT NOT NULL,
    answer_markdown TEXT NOT NULL,
    summary VARCHAR(500),
    source_doc_id VARCHAR(100),
    source_article_label VARCHAR(100),
    answer_source VARCHAR(20),
    ai_confidence DECIMAL(3,2),
    status ENUM('DRAFT', 'PUBLISHED', 'REJECTED') DEFAULT 'DRAFT',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP,
    reviewer_id BIGINT
);
```

## TODO: 향후 개선

1. **다국어 지원**: 영어/한국어 FAQ 동시 생성
2. **버전 관리**: FAQ 초안 수정 이력 추적
3. **A/B 테스트**: 여러 답변 초안 생성 후 비교
4. **피드백 루프**: 사용자 평가를 통한 품질 개선
5. **캐싱**: 동일 클러스터 재요청 시 캐시 활용

## 결론

Phase 18에서 FAQ 초안 생성 API를 구현하여, 반복 문의에 대한 FAQ를 자동으로 생성할 수 있게 되었습니다. RAG + LLM 파이프라인을 통해 참조 문서 기반의 정확한 답변을 생성하며, 관리자는 생성된 초안을 검토/수정 후 게시할 수 있습니다.
