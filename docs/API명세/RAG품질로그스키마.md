# RAG Quality Log Schema

Phase 57: RAG 품질 관측 로그 스키마 표준화

## 1. 개요

### 1.1 목적
- RAG 파이프라인 단계별 품질 지표 추적
- Query Expansion, RRF Fusion 효과 측정
- Kibana 대시보드에서 A/B 비교 가능하도록 구조화

### 1.2 로그 인덱스
```
ctrlf-ai-rag-quality-{YYYY.MM.dd}
```

---

## 2. 로그 스키마

### 2.1 공통 필드

| 필드명 | 타입 | 설명 | 예시 |
|--------|------|------|------|
| `@timestamp` | datetime | 로그 생성 시간 | `2026-01-08T02:30:00.000Z` |
| `phase` | integer | 구현 Phase 번호 | `57` |
| `request_id` | string | 요청 고유 ID | `req_abc123` |
| `user_id` | string | 사용자 ID (해시) | `user_xyz` |
| `session_id` | string | 세션 ID | `sess_001` |
| `domain` | string | 검색 도메인 | `POLICY`, `EDU` |
| `intent` | string | 분류된 의도 | `RAG`, `GREETING` |

### 2.2 Query 필드

| 필드명 | 타입 | 설명 | 예시 |
|--------|------|------|------|
| `query.original` | string | 원본 쿼리 | `비밀번호 변경` |
| `query.normalized` | string | 정규화된 쿼리 | `비밀번호 변경` |
| `query.length` | integer | 쿼리 길이 | `7` |

### 2.3 Query Expansion 필드 (Phase 57)

| 필드명 | 타입 | 설명 | 예시 |
|--------|------|------|------|
| `expansion.enabled` | boolean | 기능 활성화 여부 | `true` |
| `expansion.used` | boolean | 실제 확장 적용 여부 | `true` |
| `expansion.reason` | string | 확장/미적용 사유 | `rule_based_expansion` |
| `expansion.query` | string | 확장된 쿼리 | `비밀번호 변경 패스워드...` |
| `expansion.method` | string | 확장 방법 | `rule_based`, `llm` |

### 2.4 Search (Original) 필드

| 필드명 | 타입 | 설명 | 예시 |
|--------|------|------|------|
| `search.original.count` | integer | 검색 결과 수 | `10` |
| `search.original.min_distance` | float | 최소 L2 거리 | `1.090` |
| `search.original.avg_distance` | float | 평균 L2 거리 | `1.145` |
| `search.original.max_distance` | float | 최대 L2 거리 | `1.352` |
| `search.original.top5_doc_ids` | array | 상위 5개 doc_id | `["doc1", "doc2", ...]` |

### 2.5 Search (Expanded) 필드 (Phase 57)

| 필드명 | 타입 | 설명 | 예시 |
|--------|------|------|------|
| `search.expanded.count` | integer | 확장 검색 결과 수 | `10` |
| `search.expanded.min_distance` | float | 최소 L2 거리 | `0.985` |
| `search.expanded.avg_distance` | float | 평균 L2 거리 | `1.073` |
| `search.expanded.max_distance` | float | 최대 L2 거리 | `1.280` |
| `search.expanded.top5_doc_ids` | array | 상위 5개 doc_id | `["doc2", "doc4", ...]` |

### 2.6 RRF Fusion 필드 (Phase 57)

| 필드명 | 타입 | 설명 | 예시 |
|--------|------|------|------|
| `rrf.enabled` | boolean | 기능 활성화 여부 | `true` |
| `rrf.applied` | boolean | 실제 융합 적용 여부 | `true` |
| `rrf.k_parameter` | integer | RRF smoothing 파라미터 | `60` |
| `rrf.input_original_count` | integer | 원문 검색 결과 수 | `10` |
| `rrf.input_expanded_count` | integer | 확장 검색 결과 수 | `10` |
| `rrf.output_count` | integer | 융합 후 결과 수 | `5` |
| `rrf.common_doc_count` | integer | 양쪽 공통 문서 수 | `4` |

### 2.7 Final Result 필드

| 필드명 | 타입 | 설명 | 예시 |
|--------|------|------|------|
| `result.sources_count` | integer | 최종 Sources 수 | `5` |
| `result.min_distance` | float | 최종 최소 거리 | `0.985` |
| `result.avg_distance` | float | 최종 평균 거리 | `1.050` |
| `result.retriever_used` | string | 사용된 검색기 | `MILVUS` |
| `result.gate_action` | string | 품질 게이트 액션 | `proceed`, `soft_demote` |
| `result.top5_doc_ids` | array | 최종 상위 5개 doc_id | `["doc2", "doc1", ...]` |

### 2.8 Quality Metrics 필드

| 필드명 | 타입 | 설명 | 예시 |
|--------|------|------|------|
| `quality.distance_improvement` | float | 거리 개선율 (%) | `-9.6` |
| `quality.expansion_benefit` | boolean | 확장이 도움됐는지 | `true` |
| `quality.rrf_benefit` | boolean | RRF가 도움됐는지 | `true` |

---

## 3. 로그 레벨별 출력

### 3.1 INFO 레벨 (운영용)

```json
{
  "@timestamp": "2026-01-08T02:30:00.000Z",
  "level": "INFO",
  "phase": 57,
  "request_id": "req_abc123",
  "domain": "POLICY",
  "query.original": "비밀번호 변경",
  "expansion.used": true,
  "rrf.applied": true,
  "result.sources_count": 5,
  "result.min_distance": 0.985,
  "quality.distance_improvement": -9.6
}
```

### 3.2 DEBUG 레벨 (개발용)

```json
{
  "@timestamp": "2026-01-08T02:30:00.000Z",
  "level": "DEBUG",
  "phase": 57,
  "request_id": "req_abc123",
  "query.original": "비밀번호 변경",
  "query.normalized": "비밀번호 변경",
  "expansion.enabled": true,
  "expansion.used": true,
  "expansion.reason": "rule_based_expansion",
  "expansion.query": "비밀번호 변경 비밀번호 패스워드 변경 규칙 정책",
  "search.original.count": 10,
  "search.original.min_distance": 1.090,
  "search.original.avg_distance": 1.145,
  "search.original.top5_doc_ids": ["doc1", "doc2", "doc3", "doc4", "doc5"],
  "search.expanded.count": 10,
  "search.expanded.min_distance": 0.985,
  "search.expanded.avg_distance": 1.073,
  "rrf.applied": true,
  "rrf.input_original_count": 10,
  "rrf.input_expanded_count": 10,
  "rrf.output_count": 5,
  "result.sources_count": 5,
  "result.top5_doc_ids": ["doc2", "doc1", "doc4", "doc3", "doc5"]
}
```

---

## 4. Kibana 대시보드 쿼리 예시

### 4.1 Phase 57 효과 측정

```json
{
  "query": {
    "bool": {
      "must": [
        { "term": { "phase": 57 } },
        { "term": { "expansion.used": true } }
      ]
    }
  },
  "aggs": {
    "avg_improvement": {
      "avg": { "field": "quality.distance_improvement" }
    }
  }
}
```

### 4.2 Sources=0 케이스 추적

```json
{
  "query": {
    "bool": {
      "must": [
        { "term": { "phase": 57 } },
        { "term": { "result.sources_count": 0 } }
      ]
    }
  }
}
```

### 4.3 RRF 융합 효과 비교

```json
{
  "query": {
    "bool": {
      "must": [
        { "term": { "phase": 57 } }
      ]
    }
  },
  "aggs": {
    "by_rrf": {
      "terms": { "field": "rrf.applied" },
      "aggs": {
        "avg_distance": {
          "avg": { "field": "result.min_distance" }
        }
      }
    }
  }
}
```

---

## 5. 코드 구현 가이드

### 5.1 로그 구조체 (Python)

```python
@dataclass
class RAGQualityLog:
    """RAG 품질 로그 구조체"""

    # 공통
    phase: int = 57
    request_id: str = ""
    domain: str = ""

    # Query
    query_original: str = ""
    query_normalized: str = ""

    # Expansion (Phase 57)
    expansion_enabled: bool = True
    expansion_used: bool = False
    expansion_reason: str = ""
    expansion_query: str = ""

    # Search Original
    search_original_count: int = 0
    search_original_min_distance: float = 0.0
    search_original_avg_distance: float = 0.0

    # Search Expanded
    search_expanded_count: int = 0
    search_expanded_min_distance: float = 0.0
    search_expanded_avg_distance: float = 0.0

    # RRF
    rrf_enabled: bool = True
    rrf_applied: bool = False
    rrf_output_count: int = 0

    # Result
    result_sources_count: int = 0
    result_min_distance: float = 0.0

    # Quality
    quality_distance_improvement: float = 0.0

    def to_dict(self) -> dict:
        """Elasticsearch 인덱싱용 dict 변환"""
        return {
            "@timestamp": datetime.utcnow().isoformat(),
            "phase": self.phase,
            "request_id": self.request_id,
            "domain": self.domain,
            "query": {
                "original": self.query_original,
                "normalized": self.query_normalized,
            },
            "expansion": {
                "enabled": self.expansion_enabled,
                "used": self.expansion_used,
                "reason": self.expansion_reason,
                "query": self.expansion_query,
            },
            "search": {
                "original": {
                    "count": self.search_original_count,
                    "min_distance": self.search_original_min_distance,
                    "avg_distance": self.search_original_avg_distance,
                },
                "expanded": {
                    "count": self.search_expanded_count,
                    "min_distance": self.search_expanded_min_distance,
                    "avg_distance": self.search_expanded_avg_distance,
                },
            },
            "rrf": {
                "enabled": self.rrf_enabled,
                "applied": self.rrf_applied,
                "output_count": self.rrf_output_count,
            },
            "result": {
                "sources_count": self.result_sources_count,
                "min_distance": self.result_min_distance,
            },
            "quality": {
                "distance_improvement": self.quality_distance_improvement,
            },
        }
```

### 5.2 로깅 함수

```python
def log_rag_quality(log: RAGQualityLog):
    """RAG 품질 로그 출력"""
    logger.info(
        f"[RAGQuality] phase={log.phase} | "
        f"expansion={log.expansion_used} | "
        f"rrf={log.rrf_applied} | "
        f"sources={log.result_sources_count} | "
        f"min_dist={log.result_min_distance:.3f} | "
        f"improvement={log.quality_distance_improvement:.1f}%",
        extra=log.to_dict()
    )
```

---

## 6. 마이그레이션 계획

### 6.1 Phase 57 (현재)
- [x] 로그 스키마 정의
- [ ] `RAGQualityLog` 구조체 구현
- [ ] `rag_handler.py`에 로깅 통합
- [ ] Elasticsearch 인덱스 템플릿 생성

### 6.2 Phase 58 (예정)
- [ ] Kibana 대시보드 구성
- [ ] 알림 설정 (Sources=0 다발 시)
- [ ] 베이스라인 메트릭 수집

---

## 부록

### A. Elasticsearch 인덱스 템플릿

```json
{
  "index_patterns": ["ctrlf-ai-rag-quality-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "phase": { "type": "integer" },
        "request_id": { "type": "keyword" },
        "domain": { "type": "keyword" },
        "query.original": { "type": "text" },
        "expansion.used": { "type": "boolean" },
        "expansion.reason": { "type": "keyword" },
        "rrf.applied": { "type": "boolean" },
        "result.sources_count": { "type": "integer" },
        "result.min_distance": { "type": "float" },
        "quality.distance_improvement": { "type": "float" }
      }
    }
  }
}
```

### B. 참고 문헌
- Phase 57: Query Expansion + RRF Fusion 구현
- PERFORMANCE_IMPROVEMENT_REPORT_v9.md

---

**작성일**: 2026-01-08
**버전**: Phase 57
**작성**: CTRL+F AI 개발팀
