# Phase 4-2 개발 보고서: PII HTTP 서비스 연동 구체화

## 개요

**프로젝트**: ctrlf-ai-gateway
**Phase**: 4-2
**작성일**: 2025-12-08
**목표**: PII HTTP 서비스(GLiNER-PII 기반) 연동 구현 및 테스트

---

## 1. 구현 목표

Phase 4-2에서는 PiiService의 HTTP 연동을 구체화하고 테스트를 추가했습니다:

1. **PiiService HTTP 연동 구체화** - GLiNER-PII 기반 마이크로서비스와 실제 통신
2. **3단계 마스킹 stage 파라미터 전달** - INPUT/OUTPUT/LOG 명확한 전달
3. **Fallback 전략 강화** - 에러/미설정 시 안전한 동작 보장
4. **HTTP Mock 테스트 추가** - httpx.MockTransport를 활용한 통합 테스트

---

## 2. PII HTTP 서비스 API 스펙

### 2.1 게이트웨이 관점 API 스펙

| 항목 | 값 |
|------|-----|
| 베이스 URL | `PII_BASE_URL` (예: `http://pii-service:8003`) |
| 엔드포인트 | `POST {PII_BASE_URL}/mask` |
| 타임아웃 | 10초 |

### 2.2 요청 JSON
```json
{
  "text": "원본 또는 LLM 응답 텍스트",
  "stage": "input" | "output" | "log"
}
```

### 2.3 응답 JSON
```json
{
  "original_text": "홍길동의 전화번호는 010-1234-5678입니다.",
  "masked_text": "[PERSON]의 전화번호는 [PHONE]입니다.",
  "has_pii": true,
  "tags": [
    {"entity": "홍길동", "label": "PERSON", "start": 0, "end": 3},
    {"entity": "010-1234-5678", "label": "PHONE", "start": 10, "end": 23}
  ]
}
```

### 2.4 PII 라벨 유형 (예시)
| 라벨 | 설명 |
|------|------|
| `PERSON` | 인명 |
| `PHONE` | 전화번호 |
| `RRN` | 주민등록번호 |
| `EMAIL` | 이메일 주소 |
| `ADDRESS` | 주소 |
| `ACCOUNT` | 계좌번호 |

---

## 3. PiiService 구현 상세

### 3.1 동작 흐름

```
detect_and_mask(text, stage)
    │
    ├─[1] 빈 문자열/공백 체크
    │     └─ 해당 시 → 원문 반환 (HTTP 호출 X)
    │
    ├─[2] PII_ENABLED / PII_BASE_URL 체크
    │     └─ 미설정/비활성 시 → 원문 반환 (HTTP 호출 X)
    │
    ├─[3] HTTP POST /mask 호출
    │     ├─ 요청: {"text": "...", "stage": "input"|"output"|"log"}
    │     └─ 응답: {"original_text", "masked_text", "has_pii", "tags"}
    │
    ├─[4] 응답 파싱 → PiiMaskResult 생성
    │
    └─[5] 에러 발생 시 → Fallback (원문 반환)
```

### 3.2 Fallback 전략

| 상황 | 동작 | 이유 |
|------|------|------|
| `PII_ENABLED=False` | 원문 반환, `has_pii=False` | 명시적 비활성화 |
| `PII_BASE_URL` 미설정 | 원문 반환, `has_pii=False` | 서비스 미배포 환경 |
| 빈 문자열/공백 입력 | 원문 반환, HTTP 호출 X | 불필요한 네트워크 호출 방지 |
| HTTP 4xx/5xx 에러 | 원문 반환, 에러 로깅 | 시스템 가용성 우선 |
| 네트워크 타임아웃 | 원문 반환, 에러 로깅 | 시스템 가용성 우선 |
| JSON 파싱 실패 | 원문 반환, 에러 로깅 | 시스템 가용성 우선 |

> **Note**: PII 서비스 장애 시 민감정보가 노출될 수 있으나, 시스템 전체 중단보다 가용성을 우선합니다. 로그/모니터링으로 빠르게 감지하여 대응합니다.

### 3.3 3단계 마스킹 (MaskingStage)

| 단계 | 값 | 적용 시점 | 설명 |
|------|-----|-----------|------|
| INPUT | `"input"` | 사용자 입력 시 | RAG/LLM 전달 전 마스킹 |
| OUTPUT | `"output"` | LLM 응답 시 | 사용자 전달 전 마스킹 |
| LOG | `"log"` | 로그 저장 시 | 장기 보관용 강화 마스킹 (TODO) |

```python
# ChatService에서의 사용
# Step 2: INPUT 마스킹
pii_input = await self._pii.detect_and_mask(
    text=user_query,
    stage=MaskingStage.INPUT,
)
masked_query = pii_input.masked_text

# Step 7: OUTPUT 마스킹
pii_output = await self._pii.detect_and_mask(
    text=raw_answer,
    stage=MaskingStage.OUTPUT,
)
final_answer = pii_output.masked_text

# meta.masked 설정
has_pii = pii_input.has_pii or pii_output.has_pii
```

---

## 4. 파일 변경 내역

### 4.1 수정된 파일 (1개)

| 파일 | 변경 내용 |
|------|-----------|
| `app/services/pii_service.py` | HTTP 연동 구체화, 빈 문자열 처리 추가, 에러 핸들링 강화 |

### 4.2 추가된 파일 (1개)

| 파일 | 라인 수 | 설명 |
|------|---------|------|
| `tests/test_pii_http_integration.py` | 340 | PII HTTP 연동 테스트 (14개) |

---

## 5. PiiService 코드 구조

### 5.1 클래스 구조
```python
class PiiService:
    """PII 마스킹 서비스 (GLiNER-PII HTTP 연동)"""

    def __init__(
        self,
        base_url: Optional[str] = None,      # PII 서비스 URL
        enabled: Optional[bool] = None,       # 활성화 여부
        client: Optional[httpx.AsyncClient] = None,  # 테스트용 mock 주입
    ) -> None: ...

    async def detect_and_mask(
        self,
        text: str,
        stage: MaskingStage,
    ) -> PiiMaskResult:
        """메인 마스킹 메서드"""
        # 1. 빈 문자열/공백 체크
        # 2. PII 비활성/미설정 체크
        # 3. HTTP 서비스 호출

    async def _call_pii_service(
        self,
        text: str,
        stage: MaskingStage,
    ) -> PiiMaskResult:
        """HTTP POST /mask 호출"""

    def _create_fallback_result(self, text: str) -> PiiMaskResult:
        """안전한 폴백 결과 생성"""
```

### 5.2 HTTP 호출 코드
```python
# 요청 URL 및 payload 구성
url = f"{self._base_url.rstrip('/')}/mask"
payload = {
    "text": text,
    "stage": stage.value,  # "input" / "output" / "log"
}

# HTTP POST 요청
response = await client.post(url, json=payload)
response.raise_for_status()

# 응답 JSON 파싱
data = response.json()

# PiiMaskResult 생성
result = PiiMaskResult(
    original_text=data.get("original_text", text),
    masked_text=data.get("masked_text", text),
    has_pii=bool(data.get("has_pii", False)),
    tags=[PiiTag(...) for tag in data.get("tags", [])],
)
```

---

## 6. 테스트 결과

### 6.1 테스트 실행
```bash
$ pytest -v
============================= test session starts =============================
collected 62 items

tests/test_chat_api.py::test_chat_endpoint_returns_200 PASSED
tests/test_chat_api.py::test_chat_endpoint_returns_dummy_answer PASSED
tests/test_chat_api.py::test_chat_endpoint_meta_structure PASSED
tests/test_chat_api.py::test_chat_endpoint_with_minimal_payload PASSED
tests/test_chat_api.py::test_chat_endpoint_with_conversation_history PASSED
tests/test_chat_api.py::test_chat_endpoint_validation_error PASSED
tests/test_chat_api.py::test_chat_endpoint_invalid_role PASSED
tests/test_health.py::test_health_check_returns_200 PASSED
tests/test_health.py::test_health_check_returns_status_ok PASSED
tests/test_health.py::test_health_check_contains_app_info PASSED
tests/test_health.py::test_readiness_check_returns_200 PASSED
tests/test_health.py::test_readiness_check_returns_ready_true PASSED
tests/test_intent_and_pii.py::test_intent_policy_qa PASSED
tests/test_intent_and_pii.py::test_intent_incident_report PASSED
tests/test_intent_and_pii.py::test_intent_education_qa PASSED
tests/test_intent_and_pii.py::test_intent_general_chat PASSED
tests/test_intent_and_pii.py::test_intent_with_domain_policy PASSED
tests/test_intent_and_pii.py::test_intent_default_fallback PASSED
tests/test_intent_and_pii.py::test_pii_disabled_returns_original PASSED
tests/test_intent_and_pii.py::test_pii_no_base_url_returns_original PASSED
tests/test_intent_and_pii.py::test_pii_all_stages_work PASSED
tests/test_intent_and_pii.py::test_pii_preserves_text_on_skip PASSED
tests/test_intent_and_pii.py::test_chat_service_with_intent_and_pii PASSED
tests/test_intent_and_pii.py::test_chat_service_general_chat_route PASSED
tests/test_intent_and_pii.py::test_chat_service_incident_route PASSED
tests/test_intent_and_pii.py::test_chat_service_education_route PASSED
tests/test_intent_and_pii.py::test_chat_service_meta_has_required_fields PASSED
tests/test_intent_and_pii.py::test_masking_stage_enum_values PASSED
tests/test_intent_and_pii.py::test_masking_stage_enum_members PASSED
tests/test_intent_and_pii.py::test_intent_type_enum_values PASSED
tests/test_intent_and_pii.py::test_route_type_enum_values PASSED
tests/test_pii_http_integration.py::test_pii_service_calls_http_mask_endpoint_with_input_stage PASSED
tests/test_pii_http_integration.py::test_pii_service_calls_http_mask_endpoint_with_output_stage PASSED
tests/test_pii_http_integration.py::test_pii_service_calls_http_mask_endpoint_with_log_stage PASSED
tests/test_pii_http_integration.py::test_pii_service_http_500_error_falls_back_to_original PASSED
tests/test_pii_http_integration.py::test_pii_service_http_400_error_falls_back_to_original PASSED
tests/test_pii_http_integration.py::test_pii_service_connection_error_falls_back_to_original PASSED
tests/test_pii_http_integration.py::test_pii_service_invalid_json_response_falls_back PASSED
tests/test_pii_http_integration.py::test_pii_service_no_pii_detected_response PASSED
tests/test_pii_http_integration.py::test_pii_service_empty_string_skips_http_call PASSED
tests/test_pii_http_integration.py::test_pii_service_whitespace_only_skips_http_call PASSED
tests/test_pii_http_integration.py::test_chat_service_uses_pii_http_service_when_configured PASSED
tests/test_pii_http_integration.py::test_chat_service_pii_masked_false_when_no_pii PASSED
tests/test_pii_http_integration.py::test_chat_service_continues_when_pii_service_fails PASSED
tests/test_pii_http_integration.py::test_pii_service_multiple_entities_detected PASSED
tests/test_rag_api.py::test_rag_process_returns_200 PASSED
... (나머지 테스트 생략)

============================= 62 passed in 4.92s ==============================
```

### 6.2 테스트 요약

| 테스트 파일 | 테스트 수 | 결과 |
|-------------|----------|------|
| test_health.py | 5 | ✅ PASSED |
| test_chat_api.py | 7 | ✅ PASSED |
| test_rag_api.py | 8 | ✅ PASSED |
| test_service_fallback.py | 9 | ✅ PASSED |
| test_intent_and_pii.py | 19 | ✅ PASSED |
| **test_pii_http_integration.py** | **14** | ✅ **PASSED** |
| **총계** | **62** | ✅ ALL PASSED |

### 6.3 새 테스트 항목 (14개)

| 카테고리 | 테스트 | 설명 |
|----------|--------|------|
| HTTP Stage | test_pii_service_calls_http_mask_endpoint_with_input_stage | INPUT stage 전달 검증 |
| HTTP Stage | test_pii_service_calls_http_mask_endpoint_with_output_stage | OUTPUT stage 전달 검증 |
| HTTP Stage | test_pii_service_calls_http_mask_endpoint_with_log_stage | LOG stage 전달 검증 |
| Fallback | test_pii_service_http_500_error_falls_back_to_original | HTTP 500 에러 시 폴백 |
| Fallback | test_pii_service_http_400_error_falls_back_to_original | HTTP 400 에러 시 폴백 |
| Fallback | test_pii_service_connection_error_falls_back_to_original | 연결 에러 시 폴백 |
| Fallback | test_pii_service_invalid_json_response_falls_back | JSON 파싱 실패 시 폴백 |
| Response | test_pii_service_no_pii_detected_response | PII 미검출 응답 처리 |
| Skip | test_pii_service_empty_string_skips_http_call | 빈 문자열 HTTP 호출 스킵 |
| Skip | test_pii_service_whitespace_only_skips_http_call | 공백 문자열 HTTP 호출 스킵 |
| Integration | test_chat_service_uses_pii_http_service_when_configured | ChatService PII 연동 |
| Integration | test_chat_service_pii_masked_false_when_no_pii | meta.masked=False 검증 |
| Integration | test_chat_service_continues_when_pii_service_fails | PII 장애 시 계속 처리 |
| Multiple | test_pii_service_multiple_entities_detected | 복수 PII 엔티티 처리 |

---

## 7. 테스트 기법: httpx.MockTransport

### 7.1 Mock Transport 사용 예시
```python
import httpx

def create_mock_transport(handler):
    return httpx.MockTransport(handler)

def mock_pii_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)

    # 요청 검증
    assert str(request.url).endswith("/mask")
    assert body["stage"] in ["input", "output", "log"]

    # 응답 생성
    return httpx.Response(200, json={
        "original_text": body["text"],
        "masked_text": "[MASKED]",
        "has_pii": True,
        "tags": [{"entity": "...", "label": "PHONE", "start": 0, "end": 13}],
    })

# 테스트에서 사용
mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_pii_handler))
service = PiiService(
    base_url="http://pii-mock:8003",
    enabled=True,
    client=mock_client,  # Mock 클라이언트 주입
)
```

### 7.2 에러 시뮬레이션
```python
# HTTP 500 에러
def mock_500_handler(request):
    return httpx.Response(500, json={"error": "Internal Server Error"})

# 연결 에러
def mock_connection_error(request):
    raise httpx.ConnectError("Connection refused")

# 잘못된 JSON
def mock_invalid_json(request):
    return httpx.Response(200, content=b"not valid json")
```

---

## 8. 디렉터리 구조 (Phase 4-2 이후)

```
ctrlf-ai/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── api/v1/
│   │   ├── health.py
│   │   ├── chat.py
│   │   └── rag.py
│   ├── clients/
│   │   ├── http_client.py
│   │   ├── ragflow_client.py
│   │   └── llm_client.py
│   ├── core/
│   │   ├── config.py
│   │   └── logging.py
│   ├── models/
│   │   ├── chat.py
│   │   ├── rag.py
│   │   └── intent.py
│   └── services/
│       ├── chat_service.py
│       ├── rag_service.py
│       ├── ragflow_client.py
│       ├── llm_client.py
│       ├── intent_service.py
│       └── pii_service.py         # ← HTTP 연동 구체화
├── tests/
│   ├── test_health.py
│   ├── test_chat_api.py
│   ├── test_rag_api.py
│   ├── test_service_fallback.py
│   ├── test_intent_and_pii.py
│   └── test_pii_http_integration.py  # ← NEW
├── .env.example
├── Dockerfile
├── README.md
├── requirements.txt
├── DEVELOPMENT_REPORT_PHASE3_2.md
├── DEVELOPMENT_REPORT_PHASE4_1.md
└── DEVELOPMENT_REPORT_PHASE4_2.md    # ← NEW
```

---

## 9. 환경변수 설정 (PII 관련)

| 변수명 | 설명 | 기본값 | 예시 |
|--------|------|--------|------|
| `PII_BASE_URL` | PII 서비스 URL | (없음) | `http://pii-service:8003` |
| `PII_ENABLED` | 마스킹 활성화 여부 | `true` | `true` / `false` |

### .env 설정 예시
```bash
# PII 마스킹 서비스 활성화
PII_ENABLED=true
PII_BASE_URL=http://pii-service:8003

# 또는 개발/테스트 환경에서 비활성화
PII_ENABLED=false
```

---

## 10. 향후 계획

### 10.1 단기
- [ ] GLiNER-PII 실제 서비스 배포 및 연동 테스트
- [ ] LOG 단계 강화 마스킹 전략 구현
- [ ] PII 서비스 모니터링/알림 설정

### 10.2 중기
- [ ] PII 캐싱 전략 (동일 텍스트 중복 호출 방지)
- [ ] PII 라벨별 마스킹 정책 커스터마이징
- [ ] 비동기 배치 마스킹 지원

### 10.3 장기
- [ ] 온프레미스 PII 모델 배포 옵션
- [ ] 한국어 특화 PII 검출 모델 튜닝
- [ ] PII 검출 정확도 피드백 루프

---

## 11. 결론

Phase 4-2에서는 PiiService의 HTTP 연동을 구체화하고 철저한 테스트를 추가했습니다.

### 주요 성과
1. **GLiNER-PII 서비스 연동 스펙 확정** - API 요청/응답 구조 명확화
2. **3단계 마스킹 stage 파라미터 전달** - INPUT/OUTPUT/LOG 완전 지원
3. **강건한 Fallback 전략** - 모든 에러 상황에서 시스템 안정성 보장
4. **14개 HTTP Mock 테스트** - httpx.MockTransport 활용한 통합 테스트
5. **총 62개 테스트 통과** - 기존 48개 + 신규 14개

### 설계 원칙
- **가용성 우선**: PII 서비스 장애 시에도 전체 시스템 동작
- **테스트 가능성**: 의존성 주입을 통한 Mock 테스트 지원
- **명확한 로깅**: 에러 상황 빠른 감지 및 대응 가능

Phase 4-2 구현이 완료되어 PII HTTP 서비스와의 연동 기반이 마련되었습니다.

---

**작성자**: Claude AI Assistant
**검토일**: 2025-12-08
