# Phase 20 구현 보고서: FAQ 생성 고도화

**작성일**: 2025-12-16
**담당**: AI팀

---

## 1. 개요

Phase 20은 FAQ 생성 기능의 성능, 운영 편의성, 안전성을 개선합니다.

### 목표
- RAGFlow 검색 결과 캐싱으로 응답 속도 개선
- 배치 FAQ 생성 엔드포인트 추가
- RAGFlow 검색 결과의 PII 강차단
- ai_confidence 기반 품질 모니터링 로그

---

## 2. 구현 상세

### 2.1 Phase 20-AI-1: RAGFlow 검색 결과 캐싱

#### 신규 파일
- `app/utils/cache.py`: TTL + LRU 캐시 구현

#### 주요 기능
- **TTLCache 클래스**: 만료 시간(TTL) + LRU 교체 정책
- **make_cache_key()**: SHA256 기반 캐시 키 생성
- **비동기 안전**: asyncio Lock을 사용한 thread-safe 구현

#### 설정 (config.py)
```python
FAQ_RAG_CACHE_ENABLED: bool = True    # 캐시 활성화 여부
FAQ_RAG_CACHE_TTL_SECONDS: int = 300  # 캐시 TTL (5분)
FAQ_RAG_CACHE_MAXSIZE: int = 2048     # 최대 캐시 항목 수
```

#### 캐시 동작
1. 동일한 (dataset, query, top_k) 요청 시 캐시 조회
2. 캐시 히트: HTTP 호출 없이 캐시된 결과 반환
3. 캐시 미스: RAGFlow 호출 후 결과 캐시 저장

#### 사용 예시
```python
# 첫 번째 호출: RAGFlow HTTP 요청
results1 = await client.search_chunks("연차휴가", "POLICY")

# 두 번째 호출: 캐시 히트 (HTTP 요청 없음)
results2 = await client.search_chunks("연차휴가", "POLICY")
```

---

### 2.2 Phase 20-AI-2: 배치 FAQ 생성 엔드포인트

#### 신규 엔드포인트
```
POST /ai/faq/generate/batch
```

#### 요청 스키마
```json
{
  "items": [
    {
      "domain": "POLICY",
      "cluster_id": "cluster-001",
      "canonical_question": "연차휴가 신청 방법"
    },
    {
      "domain": "POLICY",
      "cluster_id": "cluster-002",
      "canonical_question": "출장비 정산 방법"
    }
  ],
  "concurrency": 4
}
```

#### 응답 스키마
```json
{
  "items": [
    {"status": "SUCCESS", "faq_draft": {...}, "error_message": null},
    {"status": "FAILED", "faq_draft": null, "error_message": "PII_DETECTED"}
  ],
  "total_count": 2,
  "success_count": 1,
  "failed_count": 1
}
```

#### 주요 특징
- **병렬 처리**: asyncio.gather + Semaphore로 동시성 제한
- **독립 실패 처리**: 한 항목 실패가 다른 항목에 영향 없음
- **순서 보장**: 요청 순서대로 응답 반환

#### 설정
```python
FAQ_BATCH_CONCURRENCY: int = 4  # 동시 처리 수
```

---

### 2.3 Phase 20-AI-3: 컨텍스트 PII 방어

#### 동작
RAGFlow 검색 결과 snippet에 PII가 포함되면 **즉시 실패** (강차단)

#### 에러 코드
- `PII_DETECTED_CONTEXT`: RAGFlow 스니펫에서 PII 발견

#### 검사 흐름
```
1. RAGFlow 검색 결과 수신
2. 각 snippet에 대해 PiiService.detect_and_mask() 호출
3. has_pii=True면 FaqGenerationError("PII_DETECTED_CONTEXT") 발생
4. LLM 호출 없이 조기 종료
```

#### 로깅
```
WARNING - PII detected in RAGFlow context: domain=POLICY, cluster_id=cluster-001, doc_index=0, doc_title='내부규정.pdf...', pii_count=1, pii_labels=['EMAIL']
```
- PII 원문은 로그하지 않음 (보안)
- 문서 제목, 인덱스, 도메인 정보만 기록

---

### 2.4 Phase 20-AI-4: 품질 모니터링 로그

#### 목적
ai_confidence 기반 품질 추적 및 운영 대시보드 지원

#### 설정
```python
FAQ_CONFIDENCE_WARN_THRESHOLD: float = 0.6  # 경고 임계값
```

#### 로깅 조건
| 조건 | 로그 레벨 |
|------|----------|
| SUCCESS + ai_confidence < 0.6 | WARNING |
| FAILED + LOW_RELEVANCE_CONTEXT | WARNING |
| FAILED + NO_DOCS_FOUND | WARNING |
| 기타 | INFO |

#### 로그 구조
```python
extra={
    "event": "faq_quality",
    "cluster_id": "cluster-001",
    "domain": "POLICY",
    "answer_source": "RAGFLOW",
    "ai_confidence": 0.55,
    "status": "SUCCESS",
    "error_message": null,
    "ragflow_top_score": 0.92
}
```

---

## 3. 파일 변경 요약

### 신규 파일
| 파일 | 설명 |
|------|------|
| `app/utils/cache.py` | TTL LRU 캐시 구현 |
| `tests/test_faq_cache_phase20.py` | 캐시 테스트 (12개) |
| `tests/test_faq_batch_phase20.py` | 배치 테스트 (6개) |
| `tests/test_faq_context_pii_phase20.py` | 컨텍스트 PII 테스트 (4개) |

### 수정 파일
| 파일 | 변경 내용 |
|------|----------|
| `app/core/config.py` | Phase 20 설정 추가 |
| `app/models/faq.py` | 배치 모델 추가 |
| `app/api/v1/faq.py` | 배치 엔드포인트 추가 |
| `app/clients/ragflow_search_client.py` | 캐시 적용 |
| `app/services/faq_service.py` | 컨텍스트 PII + 품질 로그 |

---

## 4. 테스트 결과

### Phase 20 신규 테스트
```
tests/test_faq_cache_phase20.py       - 12 passed
tests/test_faq_batch_phase20.py       - 6 passed
tests/test_faq_context_pii_phase20.py - 4 passed
총 22개 테스트 통과
```

### 회귀 테스트
- 기존 FAQ 테스트 (Phase 18/19): 119개 통과
- 전체 테스트 스위트: 510개 통과

---

## 5. 환경변수 요약

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `FAQ_RAG_CACHE_ENABLED` | `true` | RAGFlow 캐시 활성화 |
| `FAQ_RAG_CACHE_TTL_SECONDS` | `300` | 캐시 TTL (초) |
| `FAQ_RAG_CACHE_MAXSIZE` | `2048` | 최대 캐시 항목 수 |
| `FAQ_BATCH_CONCURRENCY` | `4` | 배치 동시 처리 수 |
| `FAQ_CONFIDENCE_WARN_THRESHOLD` | `0.6` | 품질 경고 임계값 |

---

## 6. API 변경 사항

### 기존 API (변경 없음)
```
POST /ai/faq/generate
```

### 신규 API
```
POST /ai/faq/generate/batch
```

---

## 7. 에러 코드

| 코드 | 설명 |
|------|------|
| `NO_DOCS_FOUND` | RAGFlow 검색 결과 없음 |
| `LOW_RELEVANCE_CONTEXT` | 컨텍스트와 질문 관련성 낮음 |
| `PII_DETECTED` | 입력에서 PII 검출 |
| `PII_DETECTED_OUTPUT` | LLM 출력에서 PII 검출 |
| `PII_DETECTED_CONTEXT` | **[신규]** RAGFlow 스니펫에서 PII 검출 |

---

## 8. 완료 조건 체크리스트

- [x] 단건 /ai/faq/generate는 기존과 동일 동작 (캐시 히트 시 더 빠름)
- [x] /ai/faq/generate/batch가 입력 순서대로 개별 결과 반환
- [x] RAGFlow snippet에서 PII 발견 시 PII_DETECTED_CONTEXT로 강차단
- [x] ai_confidence 낮으면 WARNING 로그 (응답 스키마 변경 없음)
- [x] 전체 테스트 스위트 통과 (Phase 20 관련)

---

## 9. 후속 작업

- 캐시 통계 모니터링 대시보드 연동 (선택)
- 배치 요청 로깅/추적 강화 (선택)
