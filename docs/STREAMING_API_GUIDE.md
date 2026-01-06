# 스트리밍 채팅 API 가이드

> **최종 수정일**: 2025-12-31
> **버전**: 2.0 (요청 형식 ChatRequest와 통일)

HTTP 청크 스트리밍으로 AI 응답을 전송하는 API

## 통신 구조

```
사용자 → 백엔드(Spring): HTTPS
백엔드 → AI서버: HTTPS (이 API)
AI서버 → 백엔드: HTTP 스트리밍 (청크드, NDJSON)
백엔드 → 프론트: SSE
```

---

## 엔드포인트

### POST /ai/chat/stream

스트리밍 방식으로 AI 응답을 생성합니다.

---

## 요청

### Content-Type
```
application/json
```

### Request Body

> **중요**: 동기 채팅 API (`/ai/chat/messages`)와 동일한 필드 구조 + `request_id`

```json
{
  "request_id": "req-uuid-001",
  "session_id": "sess-uuid-001",
  "user_id": "emp-001",
  "user_role": "EMPLOYEE",
  "department": "개발팀",
  "domain": "POLICY",
  "channel": "WEB",
  "messages": [
    {
      "role": "user",
      "content": "연차휴가 규정이 어떻게 되나요?"
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `request_id` | string | **O** | 중복 방지 / 재시도용 고유 키 (Idempotency Key) |
| `session_id` | string | **O** | 채팅 세션 ID |
| `user_id` | string | **O** | 사용자 ID (사번 등) |
| `user_role` | string | **O** | 사용자 역할: `EMPLOYEE`, `MANAGER`, `ADMIN` 등 |
| `department` | string | X | 사용자 부서 |
| `domain` | string | X | 질의 도메인: `POLICY`, `INCIDENT`, `EDUCATION` (미지정 시 AI가 판단) |
| `channel` | string | X | 요청 채널 (기본: `WEB`) |
| `messages` | array | **O** | 대화 히스토리 (마지막 요소가 최신 메시지) |

### messages 배열 구조

```json
{
  "role": "user",
  "content": "질문 내용"
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `role` | string | `user` 또는 `assistant` |
| `content` | string | 메시지 내용 |

---

## 응답

### Content-Type
```
application/x-ndjson
```

### Transfer-Encoding
```
chunked
```

---

## NDJSON 형식 (Newline Delimited JSON)

**중요 규칙:**
- 한 줄 = 한 JSON
- 각 JSON 뒤에 반드시 `\n` (줄바꿈)
- 백엔드는 줄 단위로 파싱하여 SSE로 변환

---

## 이벤트 타입

### 1. meta (시작)

연결 직후 1회 전송. 침묵 시간을 없애고 연결 상태를 확정합니다.

```json
{"type":"meta","request_id":"req-uuid-001","model":"qwen2.5-7b","timestamp":"2025-01-01T10:00:00.000000"}
```

| 필드 | 설명 |
|------|------|
| `type` | `"meta"` |
| `request_id` | 요청 ID |
| `model` | 사용 모델명 |
| `timestamp` | 시작 시간 (ISO 8601) |

---

### 2. token (토큰 스트림)

여러 번 전송. 텍스트는 **증분(delta)** 으로 전송됩니다.

```json
{"type":"token","text":"안"}
{"type":"token","text":"녕"}
{"type":"token","text":"하"}
{"type":"token","text":"세"}
{"type":"token","text":"요"}
```

| 필드 | 설명 |
|------|------|
| `type` | `"token"` |
| `text` | 토큰 텍스트 (증분, 백엔드가 이어붙임) |

---

### 3. done (정상 종료)

정상 종료 시 1회 전송.

```json
{"type":"done","finish_reason":"stop","total_tokens":123,"elapsed_ms":4567,"ttfb_ms":200}
```

| 필드 | 설명 |
|------|------|
| `type` | `"done"` |
| `finish_reason` | 종료 사유 (`"stop"`) |
| `total_tokens` | 총 토큰 수 (선택) |
| `elapsed_ms` | 총 소요 시간 (ms) |
| `ttfb_ms` | 첫 토큰까지 시간 (ms) |

---

### 4. error (에러)

에러 시 1회 전송. **에러 후 즉시 스트림 종료.**

```json
{"type":"error","code":"LLM_TIMEOUT","message":"LLM 응답 시간 초과","request_id":"req-uuid-001"}
```

| 필드 | 설명 |
|------|------|
| `type` | `"error"` |
| `code` | 에러 코드 |
| `message` | 에러 메시지 |
| `request_id` | 요청 ID |

---

## 에러 코드

| 코드 | 설명 |
|------|------|
| `LLM_TIMEOUT` | LLM 응답 시간 초과 |
| `LLM_ERROR` | LLM 서비스 오류 |
| `DUPLICATE_INFLIGHT` | 중복 요청 (이미 처리 중) |
| `INVALID_REQUEST` | 잘못된 요청 |
| `INTERNAL_ERROR` | 내부 서버 오류 |
| `CLIENT_DISCONNECTED` | 클라이언트 연결 끊김 |

---

## curl 테스트 예제

### 기본 요청

```bash
curl -X POST http://localhost:8000/ai/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "test-001",
    "session_id": "sess-001",
    "user_id": "emp-001",
    "user_role": "EMPLOYEE",
    "messages": [{"role": "user", "content": "안녕하세요"}]
  }' \
  --no-buffer
```

### 전체 필드

```bash
curl -X POST http://localhost:8000/ai/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "test-002",
    "session_id": "sess-001",
    "user_id": "emp-001",
    "user_role": "EMPLOYEE",
    "department": "개발팀",
    "domain": "POLICY",
    "channel": "WEB",
    "messages": [
      {"role": "user", "content": "연차휴가 규정 알려주세요"}
    ]
  }' \
  --no-buffer
```

### 기대 출력 (NDJSON)

```
{"type":"meta","request_id":"test-001","model":"qwen2.5-7b","timestamp":"2025-01-01T10:00:00.000000"}
{"type":"token","text":"안"}
{"type":"token","text":"녕"}
{"type":"token","text":"하"}
{"type":"token","text":"세"}
{"type":"token","text":"요"}
{"type":"token","text":"!"}
{"type":"token","text":" "}
{"type":"token","text":"무"}
{"type":"token","text":"엇"}
{"type":"token","text":"을"}
{"type":"token","text":" "}
{"type":"token","text":"도"}
{"type":"token","text":"와"}
{"type":"token","text":"드"}
{"type":"token","text":"릴"}
{"type":"token","text":"까"}
{"type":"token","text":"요"}
{"type":"token","text":"?"}
{"type":"done","finish_reason":"stop","total_tokens":18,"elapsed_ms":1234,"ttfb_ms":150}
```

---

## 중복 요청 방지

### 동작 방식

동일한 `request_id`로 요청이 이미 처리 중이면 `DUPLICATE_INFLIGHT` 에러를 반환합니다.

```json
{"type":"error","code":"DUPLICATE_INFLIGHT","message":"이미 처리 중인 요청입니다. 잠시 후 다시 시도해주세요.","request_id":"test-001"}
```

### 재시도 버튼 대응

1. 프론트에서 재시도 클릭 시 **동일한 `request_id`** 전송
2. AI 서버가 이미 처리 중이면 `DUPLICATE_INFLIGHT` 반환
3. 백엔드는 이 에러를 받으면 "잠시 후 다시 시도" 안내

### 캐시 TTL

- 완료된 요청은 10분간 캐시됨
- 캐시 만료 후 동일 `request_id`는 새 요청으로 처리

---

## 연결 끊김 처리

### 자원 낭비 방지

스트리밍 도중 백엔드 클라이언트가 연결을 끊으면:

1. AI 서버가 연결 끊김 감지
2. LLM 생성 즉시 중단
3. GPU/CPU 자원 낭비 방지

### 로그

```
INFO: Stream cancelled (client disconnected): req-uuid-001
```

---

## 메트릭

### 로그에 기록되는 정보

```json
{
  "request_id": "req-uuid-001",
  "model": "qwen2.5-7b",
  "ttfb_ms": 200,
  "total_elapsed_ms": 4567,
  "total_tokens": 123,
  "error_code": null,
  "completed": true
}
```

| 필드 | 설명 |
|------|------|
| `request_id` | 요청 ID |
| `model` | 사용 모델명 |
| `ttfb_ms` | Time To First Byte (첫 토큰까지 시간) |
| `total_elapsed_ms` | 총 소요 시간 |
| `total_tokens` | 총 토큰 수 |
| `error_code` | 에러 코드 (있으면) |
| `completed` | 완료 여부 |

### 보안/개인정보

- 요청/응답 원문은 로그에 저장하지 않음 (PII 위험)
- `request_id`와 메트릭만 기록

---

## 백엔드(Spring) 파싱 가이드

### NDJSON 파싱 규칙

1. 응답을 줄 단위로 읽음 (`\n` 기준)
2. 각 줄을 JSON으로 파싱
3. `type` 필드에 따라 처리:
   - `meta`: 연결 확정, 로깅
   - `token`: 텍스트 누적, SSE로 전송
   - `done`: 완료 처리
   - `error`: 에러 처리

### Spring WebClient 예제

```java
WebClient webClient = WebClient.create("http://ai-server:8000");

Flux<String> stream = webClient.post()
    .uri("/ai/chat/stream")
    .contentType(MediaType.APPLICATION_JSON)
    .bodyValue(request)
    .retrieve()
    .bodyToFlux(String.class)  // 줄 단위로 수신
    .map(line -> {
        JsonObject json = JsonParser.parseString(line).getAsJsonObject();
        String type = json.get("type").getAsString();

        switch (type) {
            case "meta":
                // 연결 확정
                break;
            case "token":
                // SSE로 전송
                return json.get("text").getAsString();
            case "done":
                // 완료 처리
                break;
            case "error":
                // 에러 처리
                throw new RuntimeException(json.get("message").getAsString());
        }
        return null;
    })
    .filter(Objects::nonNull);
```

---

## 타임아웃 설정 권장

| 항목 | 권장값 | 설명 |
|------|--------|------|
| 첫 토큰 (TTFB) | 5초 | 최악 3.85초 + 여유 |
| 총 응답 | 60초 | 긴 응답 대비 |
| 연결 유지 | 60초 | HTTP keep-alive |

---

## 제한사항

- WebSocket 미지원 (HTTP 스트리밍만)
- Stop(중단) 기능 없음
- 이어받기(resume) 기능 없음
- 재시도 버튼은 지원 (중복 방지)

---

## 변경 이력

| 날짜 | 버전 | 내용 |
|------|------|------|
| 2025-12-31 | 2.0 | 요청 형식을 ChatRequest와 통일 (`session_id`, `user_id`, `user_role`, `messages`) |
| 2025-01-01 | 1.0 | 초기 작성 |
