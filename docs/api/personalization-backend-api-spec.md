# Personalization Backend API Specification

**Version:** 1.1.0
**Base URL:** `http://ctrlf-back:8081`
**Last Updated:** 2025-01-18

---

## 개요

AI Gateway(FastAPI)에서 Spring 백엔드(ctrlf-back)로 개인화 데이터를 요청하는 API 명세입니다.

### 아키텍처

```
┌─────────────┐      ┌─────────────────┐      ┌──────────────┐      ┌────┐
│   사용자     │ ──→  │  AI Gateway     │ ──→  │ Spring 백엔드 │ ──→  │ DB │
│  (프론트)    │      │   (FastAPI)     │      │ (ctrlf-back) │      │    │
└─────────────┘      └─────────────────┘      └──────────────┘      └────┘
     user_id              X-User-Id 헤더            DB 조회
```

### 필수 API 목록

| # | Method | Endpoint | 설명 |
|---|--------|----------|------|
| 1 | POST | `/api/personalization/resolve` | 개인화 facts 조회 (핵심) |
| 2 | GET | `/api/org/departments/search` | 부서 검색 (Q5용) |

### 우선순위 구현 대상 (8개)

| SubIntentId | 설명 | 기본 Period | Domain |
|-------------|------|-------------|--------|
| Q1 | 미이수 필수 교육 조회 | this-year | EDU |
| Q3 | 이번 달 데드라인 필수 교육 | this-month | EDU |
| Q5 | 내 평균 vs 부서/전사 평균 | this-year | QUIZ |
| Q6 | 가장 많이 틀린 보안 토픽 TOP3 | 3m | QUIZ |
| Q9 | 이번 주 교육/퀴즈 할 일 | this-week | EDU |
| Q11 | 남은 연차 일수 | this-year | HR |
| Q14 | 복지/식대 포인트 잔액 | this-year | HR |
| Q20 | 올해 HR 할 일 (미완료) | this-year | HR |

---

## API 1: Personalization Resolve

개인화 facts 데이터를 조회합니다.

### Endpoint

```http
POST /api/personalization/resolve
```

### Headers

| Header | Type | Required | Description |
|--------|------|----------|-------------|
| `Content-Type` | string | ✅ Yes | `application/json` |
| `X-User-Id` | string | ✅ Yes | **사용자 ID** (사번, emp_id 등) |
| `Authorization` | string | ❌ No | `Bearer {token}` (인증 필요 시) |

> ⚠️ **중요**: `X-User-Id` 헤더는 **필수**입니다. 이 값으로 해당 사용자의 데이터를 DB에서 조회합니다.

### Request Body

```json
{
  "sub_intent_id": "Q11",
  "period": "this-year",
  "target_dept_id": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sub_intent_id` | string | ✅ Yes | Q1-Q20 인텐트 ID |
| `period` | string | ❌ No | 기간 유형 (미지정 시 인텐트별 기본값) |
| `target_dept_id` | string | ❌ No | 부서 비교 대상 ID (Q5에서만 사용) |

#### Period 유형

| Value | Description | 예시 |
|-------|-------------|------|
| `this-week` | 이번 주 (월~일) | 2025-01-13 ~ 2025-01-19 |
| `this-month` | 이번 달 | 2025-01-01 ~ 2025-01-31 |
| `3m` | 최근 3개월 (90일) | 2024-10-18 ~ 2025-01-18 |
| `this-year` | 올해 | 2025-01-01 ~ 현재 |

### Response (성공: 200 OK)

```json
{
  "sub_intent_id": "Q11",
  "period_start": "2025-01-01",
  "period_end": "2025-01-18",
  "updated_at": "2025-01-18T10:30:00",
  "metrics": {
    "total_days": 15,
    "used_days": 8,
    "remaining_days": 7
  },
  "items": [],
  "extra": {},
  "error": null
}
```

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| `sub_intent_id` | string | No | 요청한 인텐트 ID |
| `period_start` | string | Yes | 조회 기간 시작일 (YYYY-MM-DD) |
| `period_end` | string | Yes | 조회 기간 종료일 (YYYY-MM-DD) |
| `updated_at` | string | Yes | 데이터 최종 업데이트 시각 (ISO 8601) |
| `metrics` | object | No | 수치 데이터 (인텐트별 상이) |
| `items` | array | No | 목록 데이터 (인텐트별 상이) |
| `extra` | object | No | 추가 데이터 (인텐트별 상이) |
| `error` | object | Yes | 에러 정보 (정상 시 `null`) |

### Response (에러)

```json
{
  "sub_intent_id": "Q11",
  "period_start": null,
  "period_end": null,
  "updated_at": null,
  "metrics": {},
  "items": [],
  "extra": {},
  "error": {
    "type": "NOT_FOUND",
    "message": "해당 기간에 데이터가 없습니다."
  }
}
```

#### Error Types

| Type | Description | AI Gateway 처리 |
|------|-------------|-----------------|
| `NOT_FOUND` | 해당 기간에 데이터 없음 | "해당 기간에 조회할 데이터가 없어요." |
| `TIMEOUT` | 조회 지연 (DB 타임아웃) | "지금 조회가 지연되고 있어요." |
| `PARTIAL` | 일부 정보만 조회됨 | "일부 정보만 가져올 수 있었어요." |
| `NOT_IMPLEMENTED` | 미구현 인텐트 | "현재 데모 범위에서는 지원하지 않는 질문이에요." |

---

## 인텐트별 Response Schema

### Q1: 미이수 필수 교육 조회

**질문 예시**: "미이수 교육 알려줘", "안 들은 교육 뭐야"

```json
{
  "sub_intent_id": "Q1",
  "period_start": "2025-01-01",
  "period_end": "2025-01-18",
  "updated_at": "2025-01-18T10:30:00",
  "metrics": {
    "total_required": 5,
    "completed": 3,
    "remaining": 2
  },
  "items": [
    {
      "education_id": "EDU001",
      "title": "개인정보보호 교육",
      "deadline": "2025-01-31",
      "status": "미이수"
    },
    {
      "education_id": "EDU002",
      "title": "정보보안 교육",
      "deadline": "2025-02-15",
      "status": "미이수"
    }
  ],
  "extra": {},
  "error": null
}
```

**metrics**:
| Field | Type | Description |
|-------|------|-------------|
| `total_required` | int | 총 필수 교육 수 |
| `completed` | int | 완료한 교육 수 |
| `remaining` | int | 미이수 교육 수 |

**items[n]**:
| Field | Type | Description |
|-------|------|-------------|
| `education_id` | string | 교육 ID |
| `title` | string | 교육명 |
| `deadline` | string | 마감일 (YYYY-MM-DD) |
| `status` | string | 상태 ("미이수") |

---

### Q3: 이번 달 데드라인 필수 교육

**질문 예시**: "이번 달 마감 교육", "곧 끝나는 교육 뭐야"

```json
{
  "sub_intent_id": "Q3",
  "period_start": "2025-01-01",
  "period_end": "2025-01-31",
  "updated_at": "2025-01-18T10:30:00",
  "metrics": {
    "deadline_count": 2
  },
  "items": [
    {
      "education_id": "EDU001",
      "title": "개인정보보호 교육",
      "deadline": "2025-01-31",
      "days_left": 13
    },
    {
      "education_id": "EDU003",
      "title": "직장 내 괴롭힘 예방교육",
      "deadline": "2025-01-25",
      "days_left": 7
    }
  ],
  "extra": {},
  "error": null
}
```

**metrics**:
| Field | Type | Description |
|-------|------|-------------|
| `deadline_count` | int | 이번 달 마감 교육 수 |

**items[n]**:
| Field | Type | Description |
|-------|------|-------------|
| `education_id` | string | 교육 ID |
| `title` | string | 교육명 |
| `deadline` | string | 마감일 (YYYY-MM-DD) |
| `days_left` | int | 마감까지 남은 일수 |

---

### Q5: 내 평균 vs 부서/전사 평균

**질문 예시**: "내 평균 점수 어때", "부서 평균이랑 비교해줘"

```json
{
  "sub_intent_id": "Q5",
  "period_start": "2025-01-01",
  "period_end": "2025-01-18",
  "updated_at": "2025-01-18T10:30:00",
  "metrics": {
    "my_average": 85.5,
    "dept_average": 82.3,
    "company_average": 80.1
  },
  "items": [],
  "extra": {
    "target_dept_id": "D001",
    "target_dept_name": "개발팀"
  },
  "error": null
}
```

**metrics**:
| Field | Type | Description |
|-------|------|-------------|
| `my_average` | float | 내 평균 점수 |
| `dept_average` | float | 대상 부서 평균 점수 |
| `company_average` | float | 전사 평균 점수 |

**extra**:
| Field | Type | Description |
|-------|------|-------------|
| `target_dept_id` | string | 비교 대상 부서 ID (null이면 내 부서) |
| `target_dept_name` | string | 비교 대상 부서명 |

---

### Q6: 가장 많이 틀린 보안 토픽 TOP3

**질문 예시**: "많이 틀린 문제 뭐야", "내가 약한 부분 알려줘"

```json
{
  "sub_intent_id": "Q6",
  "period_start": "2024-10-18",
  "period_end": "2025-01-18",
  "updated_at": "2025-01-18T10:30:00",
  "metrics": {},
  "items": [
    {
      "rank": 1,
      "topic": "피싱 메일 식별",
      "wrong_rate": 35.2
    },
    {
      "rank": 2,
      "topic": "비밀번호 정책",
      "wrong_rate": 28.7
    },
    {
      "rank": 3,
      "topic": "개인정보 처리",
      "wrong_rate": 22.1
    }
  ],
  "extra": {},
  "error": null
}
```

**items[n]**:
| Field | Type | Description |
|-------|------|-------------|
| `rank` | int | 순위 (1-3) |
| `topic` | string | 토픽명 |
| `wrong_rate` | float | 오답률 (%) |

---

### Q9: 이번 주 교육/퀴즈 할 일

**질문 예시**: "이번 주 할 일 뭐야", "오늘 해야 할 교육"

```json
{
  "sub_intent_id": "Q9",
  "period_start": "2025-01-13",
  "period_end": "2025-01-19",
  "updated_at": "2025-01-18T10:30:00",
  "metrics": {
    "todo_count": 3
  },
  "items": [
    {
      "type": "education",
      "title": "정보보안 교육",
      "deadline": "2025-01-20"
    },
    {
      "type": "quiz",
      "title": "보안 퀴즈",
      "deadline": "2025-01-19"
    },
    {
      "type": "education",
      "title": "개인정보보호 교육",
      "deadline": "2025-01-21"
    }
  ],
  "extra": {},
  "error": null
}
```

**metrics**:
| Field | Type | Description |
|-------|------|-------------|
| `todo_count` | int | 할 일 총 개수 |

**items[n]**:
| Field | Type | Description |
|-------|------|-------------|
| `type` | string | 유형 (`education` \| `quiz`) |
| `title` | string | 제목 |
| `deadline` | string | 마감일 (YYYY-MM-DD) |

---

### Q11: 남은 연차 일수

**질문 예시**: "연차 며칠 남았어", "남은 휴가 알려줘"

```json
{
  "sub_intent_id": "Q11",
  "period_start": "2025-01-01",
  "period_end": "2025-01-18",
  "updated_at": "2025-01-18T10:30:00",
  "metrics": {
    "total_days": 15,
    "used_days": 8,
    "remaining_days": 7
  },
  "items": [],
  "extra": {},
  "error": null
}
```

**metrics**:
| Field | Type | Description |
|-------|------|-------------|
| `total_days` | int | 총 연차 일수 |
| `used_days` | int | 사용한 연차 일수 |
| `remaining_days` | int | 남은 연차 일수 |

---

### Q14: 복지/식대 포인트 잔액

**질문 예시**: "복지포인트 얼마야", "식대 잔액 조회"

```json
{
  "sub_intent_id": "Q14",
  "period_start": "2025-01-01",
  "period_end": "2025-01-18",
  "updated_at": "2025-01-18T10:30:00",
  "metrics": {
    "welfare_points": 150000,
    "meal_allowance": 280000
  },
  "items": [],
  "extra": {},
  "error": null
}
```

**metrics**:
| Field | Type | Description |
|-------|------|-------------|
| `welfare_points` | int | 복지 포인트 잔액 (원) |
| `meal_allowance` | int | 식대 잔액 (원) |

---

### Q20: 올해 HR 할 일 (미완료)

**질문 예시**: "올해 HR 할 일 뭐야", "미완료 인사 업무"

```json
{
  "sub_intent_id": "Q20",
  "period_start": "2025-01-01",
  "period_end": "2025-12-31",
  "updated_at": "2025-01-18T10:30:00",
  "metrics": {
    "todo_count": 4
  },
  "items": [
    {
      "type": "education",
      "title": "필수 교육 2건",
      "status": "미완료"
    },
    {
      "type": "document",
      "title": "연말정산 서류 제출",
      "deadline": "2025-01-31"
    },
    {
      "type": "survey",
      "title": "직원 만족도 조사",
      "deadline": "2025-02-28"
    },
    {
      "type": "review",
      "title": "상반기 성과 평가",
      "deadline": "2025-06-30"
    }
  ],
  "extra": {},
  "error": null
}
```

**metrics**:
| Field | Type | Description |
|-------|------|-------------|
| `todo_count` | int | 할 일 총 개수 |

**items[n]**:
| Field | Type | Description |
|-------|------|-------------|
| `type` | string | 유형 (`education`, `document`, `survey`, `review`) |
| `title` | string | 제목 |
| `status` | string | 상태 (선택) |
| `deadline` | string | 마감일 (선택, YYYY-MM-DD) |

---

## API 2: Department Search

부서를 검색합니다. Q5에서 타부서 비교 시 사용합니다.

### Endpoint

```http
GET /api/org/departments/search?query={검색어}
```

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | ✅ Yes | 검색어 (부서명) |

### Response (200 OK)

```json
{
  "items": [
    {
      "dept_id": "D001",
      "dept_name": "개발팀",
      "dept_path": "본사 > 개발본부 > 개발팀"
    },
    {
      "dept_id": "D002",
      "dept_name": "개발1팀",
      "dept_path": "본사 > 개발본부 > 개발1팀"
    }
  ]
}
```

**items[n]**:
| Field | Type | Description |
|-------|------|-------------|
| `dept_id` | string | 부서 ID |
| `dept_name` | string | 부서명 |
| `dept_path` | string | 부서 경로 (상위 부서 포함) |

---

## Spring 구현 가이드

### Controller

```java
@RestController
@RequestMapping("/api/personalization")
@RequiredArgsConstructor
@Slf4j
public class PersonalizationController {

    private final PersonalizationService personalizationService;

    @PostMapping("/resolve")
    public ResponseEntity<PersonalizationFacts> resolve(
            @RequestHeader("X-User-Id") String userId,
            @RequestBody PersonalizationResolveRequest request) {

        log.info("Personalization resolve: userId={}, subIntentId={}, period={}",
                userId, request.getSubIntentId(), request.getPeriod());

        PersonalizationFacts facts = personalizationService.resolve(
            userId,
            request.getSubIntentId(),
            request.getPeriod(),
            request.getTargetDeptId()
        );

        return ResponseEntity.ok(facts);
    }
}

@RestController
@RequestMapping("/api/org")
@RequiredArgsConstructor
public class OrganizationController {

    private final DepartmentService departmentService;

    @GetMapping("/departments/search")
    public ResponseEntity<DepartmentSearchResponse> searchDepartments(
            @RequestParam String query) {

        List<DepartmentInfo> departments = departmentService.search(query);
        return ResponseEntity.ok(new DepartmentSearchResponse(departments));
    }
}
```

### DTO

```java
// Request
@Data
public class PersonalizationResolveRequest {
    private String subIntentId;  // Q1-Q20
    private String period;        // this-week, this-month, 3m, this-year
    private String targetDeptId;  // Q5에서만 사용 (nullable)
}

// Response
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class PersonalizationFacts {
    private String subIntentId;
    private String periodStart;   // YYYY-MM-DD
    private String periodEnd;     // YYYY-MM-DD
    private String updatedAt;     // ISO 8601
    private Map<String, Object> metrics;
    private List<Map<String, Object>> items;
    private Map<String, Object> extra;
    private PersonalizationError error;  // nullable
}

@Data
@AllArgsConstructor
@NoArgsConstructor
public class PersonalizationError {
    private String type;     // NOT_FOUND, TIMEOUT, PARTIAL, NOT_IMPLEMENTED
    private String message;
}

// Department
@Data
@AllArgsConstructor
public class DepartmentInfo {
    private String deptId;
    private String deptName;
    private String deptPath;
}

@Data
@AllArgsConstructor
public class DepartmentSearchResponse {
    private List<DepartmentInfo> items;
}
```

### Service 예시 (Q11)

```java
@Service
@RequiredArgsConstructor
public class PersonalizationService {

    private final LeaveRepository leaveRepository;
    private final EducationRepository educationRepository;
    // ... other repositories

    public PersonalizationFacts resolve(
            String userId,
            String subIntentId,
            String period,
            String targetDeptId) {

        return switch (subIntentId) {
            case "Q1" -> resolveQ1(userId, period);
            case "Q3" -> resolveQ3(userId, period);
            case "Q5" -> resolveQ5(userId, period, targetDeptId);
            case "Q6" -> resolveQ6(userId, period);
            case "Q9" -> resolveQ9(userId, period);
            case "Q11" -> resolveQ11(userId, period);
            case "Q14" -> resolveQ14(userId, period);
            case "Q20" -> resolveQ20(userId, period);
            default -> notImplemented(subIntentId);
        };
    }

    private PersonalizationFacts resolveQ11(String userId, String period) {
        // 1. DB에서 연차 정보 조회
        LeaveInfo leave = leaveRepository.findByUserId(userId);

        if (leave == null) {
            return PersonalizationFacts.builder()
                .subIntentId("Q11")
                .error(new PersonalizationError("NOT_FOUND", "연차 정보가 없습니다."))
                .build();
        }

        // 2. 기간 계산
        LocalDate[] dates = calculatePeriod(period);

        // 3. Response 생성
        return PersonalizationFacts.builder()
            .subIntentId("Q11")
            .periodStart(dates[0].toString())
            .periodEnd(dates[1].toString())
            .updatedAt(LocalDateTime.now().toString())
            .metrics(Map.of(
                "total_days", leave.getTotalDays(),
                "used_days", leave.getUsedDays(),
                "remaining_days", leave.getRemainingDays()
            ))
            .items(List.of())
            .extra(Map.of())
            .error(null)
            .build();
    }

    private PersonalizationFacts notImplemented(String subIntentId) {
        return PersonalizationFacts.builder()
            .subIntentId(subIntentId)
            .error(new PersonalizationError(
                "NOT_IMPLEMENTED",
                "아직 구현되지 않은 인텐트입니다: " + subIntentId
            ))
            .build();
    }
}
```

---

## 테스트

### cURL 예시

```bash
# Q11: 남은 연차 조회
curl -X POST http://localhost:8081/api/personalization/resolve \
  -H "Content-Type: application/json" \
  -H "X-User-Id: emp12345" \
  -d '{
    "sub_intent_id": "Q11",
    "period": "this-year"
  }'

# Q14: 복지포인트 조회
curl -X POST http://localhost:8081/api/personalization/resolve \
  -H "Content-Type: application/json" \
  -H "X-User-Id: emp12345" \
  -d '{
    "sub_intent_id": "Q14"
  }'

# 부서 검색
curl "http://localhost:8081/api/org/departments/search?query=개발"
```

### AI Gateway 환경변수 설정

```bash
# .env
BACKEND_BASE_URL=http://ctrlf-back:8081
BACKEND_API_TOKEN=your-api-token  # 필요 시
```

---

## 체크리스트

### Spring 백엔드 구현 체크리스트

- [ ] `POST /api/personalization/resolve` 엔드포인트 구현
- [ ] `X-User-Id` 헤더에서 사용자 ID 추출
- [ ] Q1 (미이수 교육) 로직 구현
- [ ] Q3 (이번 달 마감 교육) 로직 구현
- [ ] Q5 (평균 비교) 로직 구현
- [ ] Q6 (틀린 토픽 TOP3) 로직 구현
- [ ] Q9 (이번 주 할 일) 로직 구현
- [ ] Q11 (남은 연차) 로직 구현
- [ ] Q14 (복지/식대 포인트) 로직 구현
- [ ] Q20 (올해 HR 할 일) 로직 구현
- [ ] `GET /api/org/departments/search` 엔드포인트 구현
- [ ] 에러 처리 (NOT_FOUND, TIMEOUT 등)
