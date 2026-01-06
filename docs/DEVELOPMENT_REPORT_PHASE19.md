# Phase 19: FAQ 초안 생성 API 고도화 보고서

## 1. 개요

### 1.1 목적
Phase 19는 FAQ 초안 생성 API(`POST /ai/faq/generate`)의 품질과 안정성을 향상시키기 위한 고도화 작업입니다.

### 1.2 범위
- Phase 19-AI-1: RagflowSearchClient 구현
- Phase 19-AI-2: 문서 검색 로직 개선
- Phase 19-AI-3: 프롬프트 템플릿 개선
- Phase 19-AI-4: PII 강차단
- Phase 19-AI-5: 통합 테스트

---

## 2. Phase 19-AI-1: RagflowSearchClient

### 2.1 구현 내용
RAGFlow 공식 API(`/v1/chunk/search`)와 연동하는 클라이언트를 구현했습니다.

**파일:** `app/clients/ragflow_search_client.py`

```python
class RagflowSearchClient:
    async def search_chunks(
        self,
        query: str,
        dataset: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """RAGFlow /v1/chunk/search API 호출"""
```

### 2.2 주요 기능
| 기능 | 설명 |
|------|------|
| HTTP 통신 | `httpx.AsyncClient`로 RAGFlow API 호출 |
| dataset 매핑 | 도메인(POLICY, EDU 등)을 kb_id로 변환 |
| 에러 처리 | `RagflowSearchError`, `RagflowConfigError` |
| 타임아웃 | 30초 기본 타임아웃 |

---

## 3. Phase 19-AI-2: 문서 검색 로직 개선

### 3.1 구현 내용
FAQ 생성 시 문서 컨텍스트 확보 로직을 개선했습니다.

**파일:** `app/services/faq_service.py`

### 3.2 동작 흐름
```
Request 수신
    ↓
top_docs 제공됨? ─Yes─→ top_docs 사용 (answer_source=TOP_DOCS)
    ↓ No
RagflowSearchClient.search_chunks() 호출
    ↓
결과 0개? ─Yes─→ NO_DOCS_FOUND 에러
    ↓ No
RAGFlow 결과 사용 (answer_source=RAGFLOW)
```

### 3.3 RagSearchResult 데이터 클래스
```python
@dataclass
class RagSearchResult:
    title: Optional[str]
    page: Optional[int]
    score: float
    snippet: str  # 최대 500자
```

---

## 4. Phase 19-AI-3: 프롬프트 템플릿 개선

### 4.1 구현 내용
LLM 프롬프트를 "근거 기반 + 짧고 명확 + 마크다운" 품질로 개선했습니다.

### 4.2 SYSTEM 프롬프트 핵심 원칙
```
1. 답변은 반드시 제공된 컨텍스트(context_docs) 범위에서만 작성한다.
2. 컨텍스트에 없는 내용은 추측하지 않는다.
3. 컨텍스트가 질문과 관련이 없다고 판단되면 status를 "LOW_RELEVANCE"로 설정한다.
```

### 4.3 출력 형식 (필드별 텍스트)
```yaml
status: SUCCESS 또는 LOW_RELEVANCE
question: [자연스러운 FAQ 질문]
summary: [1문장, 최대 120자]
answer_markdown: |
  [결론 1~2문장]
  - [핵심 규칙/절차 bullet 1]
  - [핵심 규칙/절차 bullet 2]
  **참고**
  - [문서 타이틀 (p.페이지)]
ai_confidence: [0.00~1.00]
```

### 4.4 answer_source 구분
| 값 | 조건 |
|----|------|
| `TOP_DOCS` | request.top_docs 제공됨 |
| `RAGFLOW` | RAGFlow 검색 사용 |

### 4.5 실패 규칙
| 에러 코드 | 조건 |
|-----------|------|
| `NO_DOCS_FOUND` | 컨텍스트 문서 0개 |
| `LOW_RELEVANCE_CONTEXT` | 컨텍스트가 질문과 무관 |

---

## 5. Phase 19-AI-4: PII 강차단

### 5.1 구현 내용
PII(개인식별정보) 검출 시 즉시 실패 처리하는 "강차단" 정책을 적용했습니다.

### 5.2 검사 시점
| 시점 | 검사 대상 | 에러 코드 |
|------|----------|-----------|
| 입력 | canonical_question, sample_questions, top_docs.snippet | `PII_DETECTED` |
| 출력 | answer_markdown, summary | `PII_DETECTED_OUTPUT` |

### 5.3 동작 흐름
```
Request 수신
    ↓
_check_input_pii() ─PII 검출─→ PII_DETECTED 에러
    ↓ 통과
문서 컨텍스트 확보
    ↓
LLM 호출
    ↓
응답 파싱
    ↓
_check_output_pii() ─PII 검출─→ PII_DETECTED_OUTPUT 에러
    ↓ 통과
FaqDraft 생성 및 반환
```

### 5.4 핵심 정책
- **마스킹 없음**: "마스킹해서 계속 진행"이 아닌 **즉시 실패**
- 기존 `PiiService.detect_and_mask()` 활용
- `has_pii=True` 시 예외 발생

---

## 6. Phase 19-AI-5: 통합 테스트

### 6.1 테스트 시나리오
| 시나리오 | 예상 결과 |
|----------|----------|
| top_docs 제공 시 | RagflowSearchClient 호출 안됨, answer_source=TOP_DOCS |
| top_docs 미제공 시 | search_chunks 호출, answer_source=RAGFLOW |
| 검색 결과 0개 | status=FAILED, error_message=NO_DOCS_FOUND |
| 입력 PII 포함 | status=FAILED, error_message=PII_DETECTED |
| 출력 PII 포함 | status=FAILED, error_message=PII_DETECTED_OUTPUT |
| RAGFlow 타임아웃/5xx | status=FAILED, error_message에 에러 포함 |

### 6.2 테스트 파일 구조
```
tests/
├── test_faq_service_phase19.py      # Phase 19-AI-1, AI-2 (18개)
├── test_faq_service_phase19_ai3.py  # Phase 19-AI-3 (12개)
├── test_faq_service_phase19_ai4.py  # Phase 19-AI-4 (9개)
├── test_faq_api_phase19.py          # Phase 19-AI-5 통합 (14개)
└── test_phase18_faq_generate.py     # Phase 18 호환성 (22개)
```

---

## 7. 파일 변경 요약

### 7.1 신규 파일
| 파일 | 설명 |
|------|------|
| `app/clients/ragflow_search_client.py` | RAGFlow API 클라이언트 |
| `tests/test_faq_service_phase19.py` | Phase 19-AI-1, AI-2 테스트 |
| `tests/test_faq_service_phase19_ai3.py` | Phase 19-AI-3 테스트 |
| `tests/test_faq_service_phase19_ai4.py` | Phase 19-AI-4 테스트 |
| `tests/test_faq_api_phase19.py` | Phase 19-AI-5 통합 테스트 |

### 7.2 수정 파일
| 파일 | 변경 내용 |
|------|----------|
| `app/services/faq_service.py` | 문서 검색 로직, 프롬프트 개선, PII 검사 |
| `app/models/faq.py` | answer_source에 TOP_DOCS/RAGFLOW 추가 |
| `tests/test_phase18_faq_generate.py` | Phase 19 호환성 업데이트 |

---

## 8. API 응답 예시

### 8.1 성공 응답 (top_docs 제공)
```json
{
  "status": "SUCCESS",
  "faq_draft": {
    "faq_draft_id": "550e8400-e29b-41d4-a716-446655440000",
    "domain": "SEC_POLICY",
    "cluster_id": "cluster-001",
    "question": "USB 반출 시 어떤 절차가 필요한가요?",
    "answer_markdown": "**정보보호팀의 사전 승인이 필요합니다.**\n\n- 신청서 작성\n- 승인 요청\n- 승인 후 반출\n\n**참고**\n- 정보보호규정 (p.15)",
    "summary": "정보보호팀의 사전 승인이 필요합니다.",
    "source_doc_id": "DOC-001",
    "source_doc_version": "v1",
    "source_article_label": "제3장 제2조",
    "answer_source": "TOP_DOCS",
    "ai_confidence": 0.92,
    "created_at": "2024-01-15T10:30:00Z"
  },
  "error_message": null
}
```

### 8.2 성공 응답 (RAGFlow 검색)
```json
{
  "status": "SUCCESS",
  "faq_draft": {
    "faq_draft_id": "550e8400-e29b-41d4-a716-446655440001",
    "domain": "POLICY",
    "cluster_id": "cluster-002",
    "question": "연차휴가는 이월 가능한가요?",
    "answer_markdown": "**네, 연차휴가는 다음 해로 이월이 가능합니다.**\n\n- 최대 10일까지 이월 가능\n- 12월 말까지 이월 신청 필요\n\n**참고**\n- 연차휴가규정.pdf (p.10)",
    "summary": "연차휴가는 익년도로 최대 10일까지 이월 가능합니다.",
    "source_doc_id": null,
    "source_doc_version": null,
    "source_article_label": null,
    "answer_source": "RAGFLOW",
    "ai_confidence": 0.95,
    "created_at": "2024-01-15T10:35:00Z"
  },
  "error_message": null
}
```

### 8.3 실패 응답 - NO_DOCS_FOUND
```json
{
  "status": "FAILED",
  "faq_draft": null,
  "error_message": "NO_DOCS_FOUND"
}
```

### 8.4 실패 응답 - PII_DETECTED
```json
{
  "status": "FAILED",
  "faq_draft": null,
  "error_message": "PII_DETECTED"
}
```

---

## 9. 테스트 결과

### 9.1 전체 테스트 현황
| 테스트 파일 | 테스트 수 | 결과 |
|-------------|----------|------|
| test_faq_service_phase19.py | 18 | PASSED |
| test_faq_service_phase19_ai3.py | 12 | PASSED |
| test_faq_service_phase19_ai4.py | 9 | PASSED |
| test_faq_api_phase19.py | 14 | PASSED |
| test_phase18_faq_generate.py | 22 | PASSED |
| **합계** | **75** | **ALL PASSED** |

### 9.2 커버리지
- 입력 검증: canonical_question, sample_questions, top_docs.snippet
- 출력 검증: answer_markdown, summary
- 에러 처리: NO_DOCS_FOUND, LOW_RELEVANCE_CONTEXT, PII_DETECTED, PII_DETECTED_OUTPUT
- RAGFlow 에러: 타임아웃, 5xx, 설정 오류

---

## 10. Git 커밋 이력

| 커밋 | 설명 |
|------|------|
| Phase 19-AI-1 | RagflowSearchClient 구현 |
| Phase 19-AI-2 | 문서 검색 로직 개선 |
| Phase 19-AI-3 | 프롬프트 템플릿 개선, answer_source 구분 |
| Phase 19-AI-4 | PII 강차단 |
| Phase 19-AI-5 | 통합 테스트 |

---

## 11. 향후 개선 사항

1. **캐싱**: RAGFlow 검색 결과 캐싱으로 응답 속도 향상
2. **배치 처리**: 다수 클러스터 일괄 FAQ 생성
3. **품질 모니터링**: ai_confidence 기반 자동 품질 경고
4. **A/B 테스트**: 프롬프트 버전별 품질 비교

---

## 12. 결론

Phase 19를 통해 FAQ 초안 생성 API의 품질과 안정성이 크게 향상되었습니다:

- **문서 검색 유연성**: top_docs 또는 RAGFlow 검색 선택 가능
- **프롬프트 품질**: 근거 기반, 짧고 명확한 답변 생성
- **보안 강화**: PII 강차단으로 개인정보 유출 방지
- **테스트 커버리지**: 75개 테스트로 안정성 확보

---

*작성일: 2024-12-16*
*작성자: Claude Code*
