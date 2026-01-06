# Phase 41: RAG Debug Logging

## 개요

RAG 파이프라인 디버깅을 위한 로깅 시스템 구현.
"연차 관련해서 알려줘"와 "연차휴가 알려줘" 같은 질의 차이 원인을 로그 4줄로 확인할 수 있습니다.

## 변경 파일

### 신규 파일

| 파일 | 설명 |
|------|------|
| `app/utils/debug_log.py` | RAG 디버그 로깅 유틸리티 |
| `docs/DEBUG_RAG.md` | 디버그 로깅 사용 가이드 |
| `docs/DEVELOPMENT_REPORT_PHASE41.md` | 개발 보고서 |

### 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/services/chat_service.py` | request_id 생성, route/final_query/retrieval_top5 로깅 |
| `app/clients/milvus_client.py` | retrieval_target/retrieval_top5 로깅 |

## 디버그 로그 이벤트

| 이벤트 | 위치 | 내용 |
|--------|------|------|
| `route` | chat_service.py | 라우팅 결정 결과 (intent, domain, tool, reason) |
| `retrieval_target` | milvus_client.py | Milvus 검색 대상 (collection, filter, top_k) |
| `final_query` | chat_service.py | 최종 검색 질의 (original, rewritten, keywords) |
| `retrieval_top5` | milvus_client.py / chat_service.py | 검색 결과 상위 5개 (doc_title, chunk_id, score) |

## 사용법

```powershell
# 1. 환경변수 설정
$env:DEBUG_RAG="1"

# 2. 서버 실행
uvicorn app.main:app --reload

# 3. CLI 질문
python chat_cli.py
```

## 로그 출력 예시

```
[DEBUG_RAG] {"ts":"2025-01-15T10:30:00.123","event":"route","request_id":"abc-123","user_message":"연차 관련해서 알려줘","intent":"POLICY_QA","domain":"POLICY","tool":"RAG_INTERNAL","reason":"rule-based: IntentService"}
[DEBUG_RAG] {"ts":"2025-01-15T10:30:00.125","event":"retrieval_target","request_id":"abc-123","collection":"ragflow_chunks","partition":null,"filter_expr":null,"top_k":5,"domain":"POLICY"}
[DEBUG_RAG] {"ts":"2025-01-15T10:30:00.130","event":"final_query","request_id":"abc-123","original_query":"연차 관련해서 알려줘","rewritten_query":null,"keywords":null}
[DEBUG_RAG] {"ts":"2025-01-15T10:30:00.500","event":"retrieval_top5","request_id":"abc-123","count":3,"results":[{"doc_title":"연차휴가 관리 규정","chunk_id":"doc-001","score":0.85}]}
```

## 원인 판별 가이드

1. **collection/domain이 다르면** → 라우팅/도메인 분류 문제
2. **keywords에서 핵심어 누락** → 키워드 추출/토큰화 문제
3. **top5에 관련 문서가 있는데 답변 실패** → threshold/answerability 문제
4. **top5에 관련 문서 없음** → 임베딩 유사도/데이터 문제

## 민감정보 보호

- user_message/query: 200자 제한
- 문서 본문: 로그에서 제외 (타이틀/ID/점수만)

## 테스트 결과

```
tests/test_chat_api.py - 7 passed
```
