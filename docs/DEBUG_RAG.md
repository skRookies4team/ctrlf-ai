# RAG 디버그 로깅 가이드

## 개요

RAG 파이프라인의 동작을 분석하기 위한 디버그 로그 시스템입니다.
"연차 관련해서 알려줘"와 "연차휴가 알려줘" 같은 질의가 다른 결과를 내는 원인을 로그 4줄로 확인할 수 있습니다.

## 사용법

### 1. 환경변수 설정 (PowerShell)

```powershell
$env:DEBUG_RAG="1"
```

### 2. 서버 실행

```powershell
uvicorn app.main:app --reload
```

### 3. CLI로 질문

```powershell
python chat_cli.py
```

### 4. 로그 확인

stderr에 JSON 형식으로 4가지 이벤트가 출력됩니다:

```
[DEBUG_RAG] {"ts":"2025-01-15T10:30:00.123","event":"route","request_id":"abc-123",...}
[DEBUG_RAG] {"ts":"2025-01-15T10:30:00.125","event":"retrieval_target","request_id":"abc-123",...}
[DEBUG_RAG] {"ts":"2025-01-15T10:30:00.130","event":"final_query","request_id":"abc-123",...}
[DEBUG_RAG] {"ts":"2025-01-15T10:30:00.500","event":"retrieval_top5","request_id":"abc-123",...}
```

## 로그 이벤트 설명

### 1. route (라우팅 결정)

```json
{
  "event": "route",
  "request_id": "abc-123",
  "user_message": "연차 관련해서 알려줘",
  "intent": "POLICY_QA",
  "domain": "POLICY",
  "tool": "RAG_INTERNAL",
  "reason": "rule-based: IntentService"
}
```

### 2. retrieval_target (Milvus 검색 대상)

```json
{
  "event": "retrieval_target",
  "request_id": "abc-123",
  "collection": "ragflow_chunks",
  "partition": null,
  "filter_expr": null,
  "top_k": 5,
  "domain": "POLICY"
}
```

### 3. final_query (최종 검색 질의)

```json
{
  "event": "final_query",
  "request_id": "abc-123",
  "original_query": "연차 관련해서 알려줘",
  "rewritten_query": null,
  "keywords": null
}
```

### 4. retrieval_top5 (검색 결과 상위 5개)

```json
{
  "event": "retrieval_top5",
  "request_id": "abc-123",
  "count": 5,
  "results": [
    {"doc_title": "연차휴가 관리 규정", "chunk_id": "doc-001", "score": 0.85},
    {"doc_title": "인사관리 규정", "chunk_id": "doc-002", "score": 0.72}
  ]
}
```

## 문제 원인 판별

### 케이스 비교 예시

| 질문 | collection | domain | keywords | top5 점수 |
|------|------------|--------|----------|-----------|
| 연차 관련해서 알려줘 | ragflow_chunks | POLICY | null | 0.65 |
| 연차휴가 알려줘 | ragflow_chunks | POLICY | null | 0.85 |

### 원인 판별 기준

1. **collection/partition/domain이 다르면**
   → 라우팅/도메인 분류 문제

2. **keywords에서 핵심어가 사라지면**
   → 키워드 추출/토큰화 문제 (예: 2글자 토큰 제거)

3. **top5에 관련 청크가 있는데 답이 "못 찾음"이면**
   → threshold/후처리(answerability) 문제

4. **top5에 관련 청크가 없으면**
   → 임베딩 유사도/데이터 문제

## 민감정보 보호

- `user_message`, `original_query`, `rewritten_query`: 200자 제한
- 문서 본문(text, content, snippet): 로그에서 제외
- 결과는 타이틀/ID/점수만 포함

## 비활성화

```powershell
$env:DEBUG_RAG="0"
# 또는
Remove-Item Env:DEBUG_RAG
```
