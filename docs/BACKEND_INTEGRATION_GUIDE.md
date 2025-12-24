# AI Gateway ë°±ì—”???°ë™ ê°€?´ë“œ

> **?‘ì„±??*: 2025-12-11
> **?€??*: ctrlf-back (Spring Backend) ê°œë°œ?€
> **ë²„ì „**: Phase 15 ?„ë£Œ

---

## ëª©ì°¨

1. [?„ë¡œ?íŠ¸ ê°œìš”](#1-?„ë¡œ?íŠ¸-ê°œìš”)
2. [?˜ê²½ ?¤ì • ë°??¤í–‰](#2-?˜ê²½-?¤ì •-ë°??¤í–‰)
3. [API ?”ë“œ?¬ì¸??ëª©ë¡](#3-api-?”ë“œ?¬ì¸??ëª©ë¡)
4. [ì±„íŒ… API ?°ë™ ê°€?´ë“œ](#4-ì±„íŒ…-api-?°ë™-ê°€?´ë“œ)
5. [RAG Gap ?œì•ˆ API ?°ë™ ê°€?´ë“œ](#5-rag-gap-?œì•ˆ-api-?°ë™-ê°€?´ë“œ)
6. [?ëŸ¬ ì²˜ë¦¬](#6-?ëŸ¬-ì²˜ë¦¬)
7. [?°ë™ ì²´í¬ë¦¬ìŠ¤??(#7-?°ë™-ì²´í¬ë¦¬ìŠ¤??

---

## 1. ?„ë¡œ?íŠ¸ ê°œìš”

### 1.1 AI Gateway?€?

AI Gateway???¬ìš©??ì§ˆë¬¸??ë°›ì•„ RAG(ê²€?? + LLM(?ì„±)??ê±°ì³ ?µë???ë°˜í™˜?˜ëŠ” FastAPI ?œë²„?…ë‹ˆ??

```
?Œâ??€?€?€?€?€?€?€?€?€?€?€?€??    ?Œâ??€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€??    ?Œâ??€?€?€?€?€?€?€?€?€?€?€?€??
??ctrlf-back  ?‚â??€?€?€?¶â”‚  AI Gateway     ?‚â??€?€?€?¶â”‚  RAGFlow    ??
?? (Spring)   ??    ?? (FastAPI)      ??    ?? (ê²€?‰ì—”ì§?  ??
?”â??€?€?€?€?€?€?€?€?€?€?€?€??    ?”â??€?€?€?€?€?€?€?¬â??€?€?€?€?€?€?€??    ?”â??€?€?€?€?€?€?€?€?€?€?€?€??
                             ??
                             ??
                    ?Œâ??€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€??
                    ?? LLM Server     ??
                    ?? (Qwen2.5-7B)   ??
                    ?”â??€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€??
```

### 1.2 ì£¼ìš” ê¸°ëŠ¥

| ê¸°ëŠ¥ | ?¤ëª… |
|------|------|
| ì±„íŒ… ?‘ë‹µ ?ì„± | ?¬ê·œ/êµìœ¡/?¬ê³  ê´€??ì§ˆë¬¸??AI ?µë? |
| PII ë§ˆìŠ¤??| ê°œì¸?•ë³´ ?ë™ ?ì? ë°?ë§ˆìŠ¤??|
| ??• ë³??¼ìš°??| EMPLOYEE/ADMINë³??¤ë¥¸ ì²˜ë¦¬ ë¡œì§ |
| RAG Gap ë¶„ì„ | ë¬¸ì„œ ë¶€ì¡?ì§ˆë¬¸ ?ë³„ ë°?ë³´ì™„ ?œì•ˆ |

---

## 2. ?˜ê²½ ?¤ì • ë°??¤í–‰

### 2.1 ?¬ì „ ?”êµ¬?¬í•­

- Python 3.12.7
- pip (?¨í‚¤ì§€ ê´€ë¦¬ì)

### 2.2 ?¤ì¹˜ ë°??¤í–‰

```bash
# 1. ?„ë¡œ?íŠ¸ ?´ë¡ 
git clone https://github.com/skRookies4team/ctrlf-ai.git
cd ctrlf-ai

# 2. ê°€?í™˜ê²??ì„± ë°??œì„±??
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. ?˜ì¡´???¤ì¹˜
pip install -r requirements.txt

# 4. ?˜ê²½ë³€???¤ì •
cp .env.example .env
# .env ?Œì¼ ?¸ì§‘?˜ì—¬ ?„ìš”??ê°??¤ì •

# 5. ?œë²„ ?¤í–‰
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2.3 ?˜ê²½ë³€??(.env)

```env
# ???¤ì •
APP_NAME=ctrlf-ai-gateway
APP_ENV=development

# LLM ?œë²„ (?„ìˆ˜)
LLM_BASE_URL=http://your-llm-server:port/v1

# RAGFlow ?œë²„ (RAG ê²€?‰ìš©)
RAGFLOW_BASE_URL=http://your-ragflow-server:9380

# PII ë§ˆìŠ¤???œë²„ (? íƒ)
PII_BASE_URL=http://your-pii-server:8000
PII_ENABLED=true

# ë°±ì—”???œë²„ (ë¡œê·¸ ?„ì†¡??
BACKEND_BASE_URL=http://localhost:8080
```

### 2.4 API ë¬¸ì„œ ?•ì¸

?œë²„ ?¤í–‰ ??ë¸Œë¼?°ì??ì„œ:

| URL | ?¤ëª… |
|-----|------|
| http://localhost:8000/docs | **Swagger UI** (ì¶”ì²œ) |
| http://localhost:8000/redoc | ReDoc ë¬¸ì„œ |
| http://localhost:8000/health | ?¬ìŠ¤ì²´í¬ |

---

## 3. API ?”ë“œ?¬ì¸??ëª©ë¡

| ?”ë“œ?¬ì¸??| ë©”ì„œ??| ?¤ëª… | ?©ë„ |
|-----------|--------|------|------|
| `/health` | GET | ?œë²„ ?íƒœ ?•ì¸ | ?¬ìŠ¤ì²´í¬ |
| `/health/ready` | GET | ì¤€ë¹??íƒœ ?•ì¸ | K8s Readiness |
| `/ai/chat/messages` | POST | **ì±„íŒ… ?‘ë‹µ ?ì„±** | ë©”ì¸ ì±„íŒ… API |
| `/ai/rag/process` | POST | RAG ë¬¸ì„œ ì²˜ë¦¬ | ?´ë???|
| `/ai/gap/policy-edu/suggestions` | POST | RAG Gap ë³´ì™„ ?œì•ˆ | ê´€ë¦¬ì??|

---

## 4. ì±„íŒ… API ?°ë™ ê°€?´ë“œ

### 4.1 ê¸°ë³¸ ?Œë¡œ??

```
[?¬ìš©?? ??[ctrlf-back] ??[AI Gateway] ??[RAGFlow + LLM] ??[AI Gateway] ??[ctrlf-back] ??[?¬ìš©??
```

**?ì„¸ ?Œë¡œ??**

```
1. ?¬ìš©?ê? ?„ë¡ ?¸ì—”?œì—??ì§ˆë¬¸ ?…ë ¥
2. ctrlf-back???¬ìš©???•ë³´?€ ?¨ê»˜ AI Gateway ?¸ì¶œ
3. AI Gateway ì²˜ë¦¬:
   ?œâ? PII ë§ˆìŠ¤??(ê°œì¸?•ë³´ ?œê±°)
   ?œâ? Intent ë¶„ë¥˜ (ì§ˆë¬¸ ? í˜• ?Œì•…)
   ?œâ? RAG ê²€??(ê´€??ë¬¸ì„œ ì°¾ê¸°)
   ?œâ? LLM ?‘ë‹µ ?ì„±
   ?”â? PII ë§ˆìŠ¤??(?‘ë‹µ?ì„œ ê°œì¸?•ë³´ ?œê±°)
4. AI Gateway ??ctrlf-back ?‘ë‹µ ë°˜í™˜
5. ctrlf-back ???„ë¡ ?¸ì—”?????¬ìš©??
```

### 4.2 ?”ì²­ ?¤í™

**?”ë“œ?¬ì¸??** `POST /ai/chat/messages`

**Request Body:**

```json
{
  "session_id": "sess-uuid-1234",
  "user_id": "emp-001",
  "user_role": "EMPLOYEE",
  "domain": "POLICY",
  "department": "ê°œë°œ?€",
  "channel": "WEB",
  "messages": [
    {
      "role": "user",
      "content": "?°ì°¨ ?´ì›” ê·œì •???´ë–»ê²??˜ë‚˜??"
    }
  ]
}
```

**?„ë“œ ?¤ëª…:**

| ?„ë“œ | ?€??| ?„ìˆ˜ | ?¤ëª… |
|------|------|------|------|
| `session_id` | string | ??| ?¸ì…˜ ?ë³„??(?€??ì»¨í…?¤íŠ¸ ? ì??? |
| `user_id` | string | ??| ?¬ìš©??ID |
| `user_role` | string | ??| ??• : `EMPLOYEE`, `ADMIN`, `INCIDENT_MANAGER` |
| `domain` | string | ??| ?„ë©”???ŒíŠ¸: `POLICY`, `EDU`, `INCIDENT` |
| `department` | string | ??| ë¶€?œëª… |
| `channel` | string | ??| ì±„ë„: `WEB`, `MOBILE`, `SLACK` |
| `messages` | array | ??| ë©”ì‹œì§€ ë°°ì—´ (ìµœì†Œ 1ê°? |
| `messages[].role` | string | ??| `user` ?ëŠ” `assistant` |
| `messages[].content` | string | ??| ë©”ì‹œì§€ ?´ìš© |

### 4.3 ?‘ë‹µ ?¤í™

**Response Body:**

```json
{
  "answer": "?°ì°¨?´ê????¤ìŒ ??ë§ì¼ê¹Œì? ìµœë? 10?¼ê¹Œì§€ ?´ì›”?????ˆìŠµ?ˆë‹¤.\n\n[ì°¸ê³  ê·¼ê±°]\n- ?°ì°¨?´ê? ê´€ë¦?ê·œì • ??0ì¡?(?°ì°¨ ?´ì›”) ????,
  "sources": [
    {
      "doc_id": "doc-001",
      "title": "?°ì°¨?´ê? ê´€ë¦?ê·œì •",
      "page": 5,
      "score": 0.92,
      "snippet": "?°ì°¨???¤ìŒ ??ë§ì¼ê¹Œì? ìµœë? 10?¼ê¹Œì§€ ?´ì›”?????ˆë‹¤...",
      "article_label": "??0ì¡?(?°ì°¨ ?´ì›”) ????,
      "article_path": "????> ??0ì¡?> ????
    }
  ],
  "meta": {
    "user_role": "EMPLOYEE",
    "used_model": "internal-llm",
    "route": "RAG_INTERNAL",
    "intent": "POLICY_QA",
    "domain": "POLICY",
    "masked": false,
    "has_pii_input": false,
    "has_pii_output": false,
    "rag_used": true,
    "rag_source_count": 1,
    "latency_ms": 1250,
    "rag_latency_ms": 350,
    "llm_latency_ms": 850,
    "rag_gap_candidate": false
  }
}
```

**?‘ë‹µ ?„ë“œ ?¤ëª…:**

| ?„ë“œ | ?€??| ?¤ëª… |
|------|------|------|
| `answer` | string | AI ?ì„± ?µë? |
| `sources` | array | RAG ê²€??ê²°ê³¼ (ê·¼ê±° ë¬¸ì„œ) |
| `sources[].doc_id` | string | ë¬¸ì„œ ID |
| `sources[].title` | string | ë¬¸ì„œ ?œëª© |
| `sources[].score` | float | ê´€?¨ë„ ?ìˆ˜ (0~1) |
| `sources[].snippet` | string | ë°œì·Œ ?´ìš© |
| `sources[].article_label` | string | ì¡°í•­ ?¼ë²¨ (?? ??0ì¡????? |
| `meta.route` | string | ì²˜ë¦¬ ê²½ë¡œ |
| `meta.intent` | string | ë¶„ë¥˜???˜ë„ |
| `meta.rag_used` | boolean | RAG ?¬ìš© ?¬ë? |
| `meta.latency_ms` | int | ?„ì²´ ì²˜ë¦¬ ?œê°„ (ms) |
| `meta.rag_gap_candidate` | boolean | RAG Gap ?„ë³´ ?¬ë? |

### 4.4 ??• ë³?ì²˜ë¦¬ ì°¨ì´

| ??•  | ?¤ëª… | ?¹ì§• |
|------|------|------|
| `EMPLOYEE` | ?¼ë°˜ ì§ì› | ?¬ê·œ ì§ˆì˜, êµìœ¡ ?„í™© ì¡°íšŒ, ?¬ê³  ? ê³  |
| `ADMIN` | ê´€ë¦¬ì | ë¶€???µê³„ ì¡°íšŒ, ?„ì²´ ?„í™© ?Œì•… |
| `INCIDENT_MANAGER` | ?¬ê³  ?´ë‹¹??| ?¬ê³  ?„í™© ì¡°íšŒ, ?ì„¸ ë¶„ì„ |

### 4.5 Intent(?˜ë„) ì¢…ë¥˜

| Intent | ?¤ëª… | Route |
|--------|------|-------|
| `POLICY_QA` | ?¬ê·œ/?•ì±… ì§ˆë¬¸ | RAG_INTERNAL |
| `EDUCATION_QA` | êµìœ¡ ?´ìš© ì§ˆë¬¸ | RAG_INTERNAL |
| `EDU_STATUS` | êµìœ¡ ?„í™© ì¡°íšŒ | BACKEND_API |
| `INCIDENT_REPORT` | ?¬ê³  ? ê³  | BACKEND_API |
| `INCIDENT_QA` | ?¬ê³  ê´€??ì§ˆë¬¸ | MIXED_BACKEND_RAG |
| `GENERAL_CHAT` | ?¼ë°˜ ?€??| LLM_ONLY |

### 4.6 Java/Spring ?°ë™ ?ˆì‹œ

```java
@Service
public class AiGatewayClient {

    private final WebClient webClient;

    public AiGatewayClient(@Value("${ai.gateway.url}") String baseUrl) {
        this.webClient = WebClient.builder()
            .baseUrl(baseUrl)
            .build();
    }

    public Mono<ChatResponse> chat(ChatRequest request) {
        return webClient.post()
            .uri("/ai/chat/messages")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(request)
            .retrieve()
            .bodyToMono(ChatResponse.class);
    }
}

// DTO
@Data
public class ChatRequest {
    private String sessionId;
    private String userId;
    private String userRole;
    private String domain;
    private String department;
    private List<Message> messages;

    @Data
    public static class Message {
        private String role;
        private String content;
    }
}

@Data
public class ChatResponse {
    private String answer;
    private List<Source> sources;
    private Meta meta;

    @Data
    public static class Source {
        private String docId;
        private String title;
        private Integer page;
        private Double score;
        private String snippet;
        private String articleLabel;
        private String articlePath;
    }

    @Data
    public static class Meta {
        private String userRole;
        private String route;
        private String intent;
        private String domain;
        private Boolean ragUsed;
        private Integer ragSourceCount;
        private Integer latencyMs;
        private Boolean ragGapCandidate;
    }
}
```

### 4.7 curl ?ŒìŠ¤???ˆì‹œ

```bash
# ê¸°ë³¸ ì±„íŒ… ?”ì²­
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-001",
    "user_id": "emp-001",
    "user_role": "EMPLOYEE",
    "domain": "POLICY",
    "messages": [
      {"role": "user", "content": "?°ì°¨ ?´ì›” ê·œì •???´ë–»ê²??˜ë‚˜??"}
    ]
  }'

# êµìœ¡ ?„í™© ì¡°íšŒ (EMPLOYEE)
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-002",
    "user_id": "emp-001",
    "user_role": "EMPLOYEE",
    "messages": [
      {"role": "user", "content": "??êµìœ¡ ?´ìˆ˜ ?„í™© ?Œë ¤ì¤?}
    ]
  }'

# ê´€ë¦¬ì ë¶€???µê³„ ì¡°íšŒ
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-003",
    "user_id": "admin-001",
    "user_role": "ADMIN",
    "department": "ê°œë°œ?€",
    "messages": [
      {"role": "user", "content": "?°ë¦¬ ë¶€??êµìœ¡ ?´ìˆ˜???Œë ¤ì¤?}
    ]
  }'
```

---

## 5. RAG Gap ?œì•ˆ API ?°ë™ ê°€?´ë“œ

### 5.1 ?©ë„

- ê´€ë¦¬ì ?€?œë³´?œì—??"RAG Gap ì§ˆë¬¸"???˜ì§‘????
- AI Gateway??ë³´ë‚´ë©?"?´ë–¤ ?¬ê·œ/êµìœ¡??ë³´ì™„?˜ë©´ ì¢‹ì„ì§€" ?œì•ˆ

### 5.2 ?”ì²­ ?¤í™

**?”ë“œ?¬ì¸??** `POST /ai/gap/policy-edu/suggestions`

**Request Body:**

```json
{
  "timeRange": {
    "from": "2025-12-01T00:00:00",
    "to": "2025-12-10T23:59:59"
  },
  "domain": "POLICY",
  "questions": [
    {
      "questionId": "log-123",
      "text": "?¬íƒê·¼ë¬´????VPN ???°ë©´ ?´ë–»ê²??˜ë‚˜??",
      "userRole": "EMPLOYEE",
      "intent": "POLICY_QA",
      "domain": "POLICY",
      "askedCount": 5
    },
    {
      "questionId": "log-456",
      "text": "ê°œì¸ ?´ë??°ìœ¼ë¡??Œì‚¬ ë©”ì¼ ë³´ë©´ ë³´ì•ˆ ?„ë°˜?¸ê???",
      "userRole": "EMPLOYEE",
      "intent": "POLICY_QA",
      "domain": "POLICY",
      "askedCount": 3
    }
  ]
}
```

### 5.3 ?‘ë‹µ ?¤í™

```json
{
  "summary": "?¬íƒê·¼ë¬´ ??ë³´ì•ˆ ê·œì •ê³?BYOD ?•ì±…???€??ë¬¸ì„œê°€ ë¶€ì¡±í•©?ˆë‹¤.",
  "suggestions": [
    {
      "id": "SUG-001",
      "title": "?¬íƒê·¼ë¬´ ???•ë³´ë³´í˜¸ ?˜ì¹™ ?ì„¸ ?ˆì‹œ ì¶”ê?",
      "description": "VPN ?¬ìš© ?˜ë¬´, ê³µìš© Wi-Fi ê¸ˆì?, ?”ë©´ ? ê¸ˆ ê¸°ì? ?±ì„ ?¬í•¨??ì¡°ë¬¸??? ì„¤?˜ëŠ” ê²ƒì´ ì¢‹ìŠµ?ˆë‹¤.",
      "relatedQuestionIds": ["log-123"],
      "priority": "HIGH"
    },
    {
      "id": "SUG-002",
      "title": "ê°œì¸ ?´ë????¸íŠ¸ë¶??¬ìš© ê°€?´ë“œ ì¡°í•­ ? ì„¤",
      "description": "BYOD(Bring Your Own Device) ?•ì±…??ëª…í™•???˜ê³ , ?´ë–¤ ê²½ìš°ê°€ ?„ë°˜?¸ì? ?ˆì‹œë¥?ì¶”ê??´ì•¼ ?©ë‹ˆ??",
      "relatedQuestionIds": ["log-456"],
      "priority": "MEDIUM"
    }
  ]
}
```

---

## 6. ?ëŸ¬ ì²˜ë¦¬

### 6.1 HTTP ?íƒœ ì½”ë“œ

| ì½”ë“œ | ?¤ëª… | ?€??|
|------|------|------|
| 200 | ?±ê³µ | ?•ìƒ ì²˜ë¦¬ |
| 400 | ?˜ëª»???”ì²­ | ?”ì²­ ?°ì´???•ì¸ |
| 422 | ? íš¨??ê²€???¤íŒ¨ | ?„ìˆ˜ ?„ë“œ ?„ë½ ?•ì¸ |
| 500 | ?œë²„ ?´ë? ?¤ë¥˜ | ?¬ì‹œ???ëŠ” fallback |
| 503 | ?œë¹„??ë¶ˆê? | LLM/RAG ?œë²„ ?íƒœ ?•ì¸ |

### 6.2 ?ëŸ¬ ?‘ë‹µ ?ˆì‹œ

```json
{
  "detail": "Validation error: messages field is required"
}
```

### 6.3 Fallback ?‘ë‹µ

LLM/RAG ?¥ì•  ?œì—???‘ë‹µ?€ ë°˜í™˜?©ë‹ˆ??

```json
{
  "answer": "ì£„ì†¡?©ë‹ˆ?? ?„ì¬ AI ?œë¹„?¤ì— ?¼ì‹œ?ì¸ ë¬¸ì œê°€ ë°œìƒ?ˆìŠµ?ˆë‹¤. ? ì‹œ ???¤ì‹œ ?œë„??ì£¼ì„¸??",
  "sources": [],
  "meta": {
    "route": "ERROR",
    "error_type": "UPSTREAM_TIMEOUT",
    "error_message": "LLM service timeout"
  }
}
```

---

## 7. ?°ë™ ì²´í¬ë¦¬ìŠ¤??

### 7.1 ê¸°ë³¸ ?°ë™

- [ ] AI Gateway ?œë²„ URL ?¤ì •
- [ ] `/health` ?”ë“œ?¬ì¸?¸ë¡œ ?°ê²° ?•ì¸
- [ ] `/ai/chat/messages` ê¸°ë³¸ ?¸ì¶œ ?ŒìŠ¤??
- [ ] ?‘ë‹µ ?Œì‹± ë°??”ë©´ ?œì‹œ

### 7.2 ?¬ìš©???•ë³´ ?°ë™

- [ ] `session_id` ?ì„± ë°?ê´€ë¦?
- [ ] `user_id` ?„ë‹¬
- [ ] `user_role` ë§¤í•‘ (EMPLOYEE/ADMIN/INCIDENT_MANAGER)
- [ ] `department` ?„ë‹¬ (? íƒ)

### 7.3 ?€??ì»¨í…?¤íŠ¸

- [ ] ?´ì „ ?€???´ì—­ `messages` ë°°ì—´ë¡??„ë‹¬
- [ ] ë©€?°í„´ ?€???ŒìŠ¤??

### 7.4 ?ëŸ¬ ì²˜ë¦¬

- [ ] HTTP ?ëŸ¬ ?¸ë“¤ë§?
- [ ] Fallback ?‘ë‹µ ì²˜ë¦¬
- [ ] ?€?„ì•„???¤ì • (ê¶Œì¥: 30ì´?

### 7.5 ë¡œê¹…/ëª¨ë‹ˆ?°ë§

- [ ] AI ë¡œê·¸ ?˜ì‹  API êµ¬í˜„ (? íƒ)
- [ ] `meta.rag_gap_candidate=true` ì§ˆë¬¸ ?˜ì§‘

---

## ë¬¸ì˜

AI Gateway ê´€??ë¬¸ì˜?¬í•­?€ AI ?€???°ë½?´ì£¼?¸ìš”.

- GitHub: https://github.com/skRookies4team/ctrlf-ai
- Swagger: http://[AI_GATEWAY_HOST]:8000/docs
