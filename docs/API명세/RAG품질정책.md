# RAG Quality Policy - Phase 58

L2 Distance 기반 RAG 품질 게이트 정책

## 1. 목표

SOFT_DEMOTE로 인해 "키워드 미매칭이어도 source 1개 유지"가 생긴 대신, 극단적 저품질(source 1개, L2=1.8 같은) 케이스에서 오히려 환각 위험이 커졌습니다.

**해결책**: L2 distance 기반 품질 게이트를 추가해서,
- 너무 낮은 관련성이면 LLM 생성 자체를 차단하고
- 명확화(구체화) 질문을 유도합니다.

## 2. Decision Table

| 조건 (min_L2_distance) | 등급 | 동작 | 응답 전략 |
|------------------------|------|------|-----------|
| <= 1.4 | OK | PROCEED | 정상 RAG 답변 |
| 1.4 < d <= 1.6 | LOW | PROCEED_WITH_WARNING | sources 사용 + "근거 제한" 경고 포함 |
| > 1.6 | INSUFFICIENT | REJECT | sources 미사용, "근거 부족 → 질문 구체화" 응답 |
| sources == 0 | INSUFFICIENT | REJECT | sources 미사용, "근거 부족 → 질문 구체화" 응답 |

### 추가 규칙
- sources == 0이면 무조건 INSUFFICIENT
- 경계값은 환경변수로 조절 가능

## 3. 환경변수 설정

```bash
# 품질 게이트 활성화 (기본: true)
RAG_QUALITY_DISTANCE_GATE_ENABLED=true

# 경고 임계값 (기본: 1.4)
RAG_QUALITY_L2_WARN=1.4

# 거부 임계값 (기본: 1.6)
RAG_QUALITY_L2_REJECT=1.6
```

## 4. 적용 위치

최종 sources 결정 이후 (원문 검색 → 확장 검색 → RRF → SOFT_DEMOTE까지 끝난 뒤) 적용합니다.

```
[Query]
   ↓
[Milvus Search (Original)]
   ↓
[Query Expansion + Milvus Search (Expanded)]
   ↓
[RRF Fusion]
   ↓
[Low-relevance Gate (SOFT_DEMOTE)]
   ↓
[Quality Gate (Phase 58)]  ← 여기서 적용
   ↓
[LLM Generation or Clarify Response]
```

## 5. 응답 형태

### 5.1 INSUFFICIENT (근거 부족)

LLM 호출 없이 고정 템플릿 반환:

```
죄송합니다. 현재 질문에 대해 충분한 근거를 찾지 못했습니다.
다음과 같이 질문을 구체화해 주시면 더 정확한 답변을 드릴 수 있습니다:

  1. 어떤 절차나 규정에 대해 알고 싶으신가요?
  2. 특정 조건이나 예외 사항이 궁금하신가요?
  3. 신청 방법이나 기한에 대해 알고 싶으신가요?
  4. 구체적인 사례나 상황을 말씀해 주시겠어요?
```

### 5.2 LOW (경고 포함)

정상 RAG 답변 + 경고 메시지:

```
[정상 RAG 답변]

※ 참고: 관련 문서가 제한적이어서 답변의 정확도가 낮을 수 있습니다.
더 구체적인 질문을 해주시면 정확한 답변을 드릴 수 있습니다.
```

## 6. 로그 스키마

Kibana에서 필터링/집계 가능한 필드:

```json
{
  "@timestamp": "2026-01-08T12:00:00Z",
  "phase": 58,
  "quality_gate": {
    "enabled": true,
    "grade": "INSUFFICIENT",
    "action": "REJECT",
    "min_l2_distance": 1.72,
    "warn_threshold": 1.4,
    "reject_threshold": 1.6,
    "insufficient_evidence": true
  }
}
```

### Kibana 쿼리 예시

```
# REJECT된 요청 조회
quality_gate.action: "REJECT"

# 근거 부족 요청 비율
quality_gate.insufficient_evidence: true

# 임계값 근처 요청 (튜닝용)
quality_gate.min_l2_distance: [1.3 TO 1.7]
```

## 7. Golden Query 회귀테스트 연동

### 기존 규칙
```
sources == 0 → 실패 (HR, SECURITY, HARASSMENT 도메인)
```

### 변경된 규칙 (Phase 58)
```
sources == 0 AND insufficient_evidence == false → 실패
sources == 0 AND insufficient_evidence == true → 통과 (의도적 REJECT)
```

**핵심**: "근거 없이 단정 답변"은 잡아내되, "의도적으로 근거 부족 응답"은 허용합니다.

## 8. 임계값 근거

| 임계값 | 근거 |
|--------|------|
| 1.4 (WARN) | L2=1.2~1.5는 "중간" 관련성. 1.4 이상은 신뢰도 저하 시작점 |
| 1.6 (REJECT) | L2=1.5+ 는 "관련성 낮음". 1.6 이상은 환각 위험 급증 구간 |

### 튜닝 가이드
- 미탐(False Negative) 많으면: REJECT 임계값 ↑
- 오탐(False Positive) 많으면: REJECT 임계값 ↓
- 운영 로그 분석 후 분기별 재검토 권장

## 9. 관련 파일

| 파일 | 설명 |
|------|------|
| `app/core/config.py` | 환경변수 설정 |
| `app/services/chat/quality_gate.py` | 품질 게이트 모듈 |
| `app/services/chat/rag_handler.py` | 게이트 통합 |
| `app/services/chat/rag_quality_log.py` | 로그 스키마 |
| `tests/unit/test_quality_gate.py` | 단위 테스트 |
| `tests/regression/test_rag_quality_regression.py` | 회귀 테스트 |

## 10. 완료 기준 (Definition of Done)

- [x] INSUFFICIENT 케이스에서 LLM 생성이 실행되지 않는다
- [x] INSUFFICIENT 응답은 "근거 부족" 메시지 + 구체화 예시 포함
- [x] LOW 케이스는 sources 사용 + "관련 문서 제한" 경고 포함
- [ ] Golden Query 회귀테스트가 의도적 REJECT 허용
- [x] Kibana에서 quality_action으로 필터링/집계 가능
