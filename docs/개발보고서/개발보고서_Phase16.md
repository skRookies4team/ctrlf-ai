# Phase 16: 퀴즈 자동 생성 API 구현

## 개요

Phase 16에서는 **퀴즈 자동 생성 API**를 구현했습니다. 백엔드(ctrlf-back)에서 교육/사규 문서의 QUIZ_CANDIDATE 블록들을 보내면, LLM이 객관식 퀴즈(문제/보기/정답/난이도/출처 메타)를 자동 생성하여 반환합니다.

이 API는 1차/2차 응시 시나리오를 지원하며, 2차 응시 시에는 1차 때 사용한 문항과의 중복을 방지합니다.

## 구현 내용

### 1. 새 엔드포인트

```
POST /ai/quiz/generate
```

### 2. 요청/응답 스키마

**요청 (QuizGenerateRequest)**:
```json
{
  "educationId": "EDU-SEC-2025-001",
  "docId": "DOC-SEC-001",
  "docVersion": "v1",
  "attemptNo": 1,
  "language": "ko",
  "numQuestions": 10,
  "difficultyDistribution": {
    "easy": 5,
    "normal": 3,
    "hard": 2
  },
  "questionType": "MCQ_SINGLE",
  "maxOptions": 4,
  "quizCandidateBlocks": [
    {
      "blockId": "BLOCK-001",
      "chapterId": "CH1",
      "learningObjectiveId": "LO-1",
      "text": "USB 메모리를 사외로 반출할 때에는 정보보호팀의 사전 승인을 받아야 한다.",
      "tags": ["USB", "반출", "승인"],
      "articlePath": "제3장 > 제2조 > 제1항"
    }
  ],
  "excludePreviousQuestions": []
}
```

**응답 (QuizGenerateResponse)**:
```json
{
  "educationId": "EDU-SEC-2025-001",
  "docId": "DOC-SEC-001",
  "docVersion": "v1",
  "attemptNo": 1,
  "generatedCount": 10,
  "questions": [
    {
      "questionId": "Q-20251212-ABCD1234",
      "status": "DRAFT_AI_GENERATED",
      "questionType": "MCQ_SINGLE",
      "stem": "USB 메모리를 사외로 반출할 때 필요한 조치는 무엇인가요?",
      "options": [
        {"optionId": "OPT-1", "text": "정보보호팀의 사전 승인", "isCorrect": true},
        {"optionId": "OPT-2", "text": "팀장에게 구두 보고만 한다", "isCorrect": false},
        {"optionId": "OPT-3", "text": "개인 판단에 따라 자유롭게 반출한다", "isCorrect": false},
        {"optionId": "OPT-4", "text": "사후에만 보고하면 된다", "isCorrect": false}
      ],
      "difficulty": "EASY",
      "learningObjectiveId": "LO-1",
      "chapterId": "CH1",
      "sourceBlockIds": ["BLOCK-001"],
      "sourceDocId": "DOC-SEC-001",
      "sourceDocVersion": "v1",
      "sourceArticlePath": "제3장 > 제2조 > 제1항",
      "tags": ["USB", "반출", "승인"],
      "explanation": "USB 반출 시에는 반드시 정보보호팀의 사전 승인을 받아야 합니다.",
      "rationale": "문서 DOC-SEC-001 v1 제3장 제2조 제1항에 해당 내용이 명시되어 있습니다."
    }
  ]
}
```

### 3. 파일 구조

```
app/
├── models/
│   └── quiz_generate.py     # DTO 모델 및 Enum 정의
├── services/
│   └── quiz_generate_service.py  # LLM 호출 및 퀴즈 생성 로직
├── api/
│   └── v1/
│       └── quiz_generate.py     # FastAPI 엔드포인트
└── main.py                      # 라우터 등록

tests/
└── test_phase16_quiz_generate.py  # 38개 테스트
```

### 4. 핵심 로직

#### QuizGenerateService

```python
class QuizGenerateService:
    async def generate_quiz(
        self,
        request: QuizGenerateRequest,
    ) -> QuizGenerateResponse:
        # 1. 난이도 분배 계산
        difficulty_counts = self._calculate_difficulty_distribution(...)

        # 2. LLM 메시지 구성
        messages = self._build_llm_messages(request, difficulty_counts)

        # 3. LLM 호출
        llm_response = await self._llm.generate_chat_completion(...)

        # 4. 응답 파싱
        parsed_questions = self._parse_llm_response(llm_response)

        # 5. 정합성 검증 및 필터링
        valid_questions = self._validate_and_filter_questions(...)

        # 6. 중복 제거 (2차 응시)
        if request.exclude_previous_questions:
            valid_questions = self._filter_duplicate_questions(...)

        # 7. 결과 조립
        final_questions = self._assemble_questions(...)

        return QuizGenerateResponse(...)
```

#### 난이도 분배 계산

```python
def _calculate_difficulty_distribution(
    self,
    num_questions: int,
    distribution: Optional[DifficultyDistribution],
) -> Dict[str, int]:
    """
    - 지정된 분배가 있으면 그대로 사용
    - 합이 다르면 비율 기반으로 재계산
    - 없으면 균등 분배 (easy/normal/hard)
    """
```

#### 2차 응시 중복 방지

```python
def _filter_duplicate_questions(
    self,
    questions: List[LLMQuizQuestion],
    exclude_list: List[ExcludePreviousQuestion],
) -> List[LLMQuizQuestion]:
    """
    - 제외할 stem 목록을 정규화 (공백, 대소문자)
    - 완전 일치하는 문항 필터링
    - 프롬프트에서도 "기존 문항과 비슷한 문항 만들지 말 것" 지시
    """
```

### 5. DTO/Enum 정의

#### Enums

```python
class QuestionType(str, Enum):
    MCQ_SINGLE = "MCQ_SINGLE"  # 단일 정답 객관식

class Difficulty(str, Enum):
    EASY = "EASY"
    NORMAL = "NORMAL"
    HARD = "HARD"

class QuestionStatus(str, Enum):
    DRAFT_AI_GENERATED = "DRAFT_AI_GENERATED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
```

#### 주요 모델

| 모델 | 설명 |
|------|------|
| `QuizCandidateBlock` | 퀴즈 생성에 사용할 텍스트 블록 |
| `ExcludePreviousQuestion` | 2차 응시 시 제외할 기존 문항 |
| `DifficultyDistribution` | 난이도 분배 설정 |
| `QuizGenerateRequest` | 퀴즈 생성 요청 DTO |
| `GeneratedQuizOption` | 생성된 보기 |
| `GeneratedQuizQuestion` | 생성된 문항 |
| `QuizGenerateResponse` | 퀴즈 생성 응답 DTO |

### 6. 정합성 검증

| 검증 항목 | 처리 방식 |
|----------|----------|
| 옵션 수 < 2 | 해당 문항 제외 |
| 정답 개수 ≠ 1 | 해당 문항 제외 |
| stem 비어있음 | 해당 문항 제외 |
| LLM 호출 실패 | 빈 응답 반환 |
| JSON 파싱 실패 | 빈 문항 목록 |

### 7. LLM 프롬프트

**System Prompt**:
```
당신은 기업 정보보안/개인정보/사규 교육용 객관식 퀴즈를 설계하는 전문가입니다.

중요 원칙:
1. 정책을 새로 만들거나 왜곡하지 말고, 문서에 명시된 사실만 사용하세요.
2. 각 문항은 오직 1개의 정답만 가져야 합니다.
3. 오답 보기는 그럴듯하지만 틀린 내용이어야 합니다.
4. 문제는 명확하고 이해하기 쉬워야 합니다.
5. 반드시 지정된 JSON 포맷으로만 응답하세요.

난이도 기준:
- EASY: 문서에서 직접 찾을 수 있는 기본 사실 확인
- NORMAL: 여러 사실을 조합하거나 적용해야 하는 문제
- HARD: 상황 판단, 예외 케이스, 복합적 이해가 필요한 문제
```

## 테스트 결과

### Phase 16 테스트 (38개 통과)

```
TestQuizGenerateModels: 11개 - 모델 생성/직렬화
TestQuizGenerateService: 4개 - 서비스 로직
TestDifficultyDistribution: 5개 - 난이도 분배 계산
TestDuplicatePrevention: 2개 - 중복 방지
TestQuestionValidation: 3개 - 정합성 검증
TestQuizGenerateAPI: 6개 - API 통합
TestQuizGenerateErrorCases: 3개 - 에러 처리
TestDifficultyParsing: 4개 - 난이도 파싱
```

### 전체 테스트

```
============================= 360 passed in 7.42s =============================
```

## 변경된 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/models/quiz_generate.py` | 신규 - DTO/Enum 모델 |
| `app/services/quiz_generate_service.py` | 신규 - 퀴즈 생성 서비스 |
| `app/api/v1/quiz_generate.py` | 신규 - API 엔드포인트 |
| `app/api/v1/__init__.py` | quiz_generate 추가 |
| `app/main.py` | 라우터 등록 |
| `tests/test_phase16_quiz_generate.py` | 신규 - 38개 테스트 |

## 사용 예시

### curl 요청

```bash
curl -X POST http://localhost:8000/ai/quiz/generate \
  -H "Content-Type: application/json" \
  -d '{
    "educationId": "EDU-SEC-2025-001",
    "docId": "DOC-SEC-001",
    "numQuestions": 5,
    "quizCandidateBlocks": [
      {
        "blockId": "BLOCK-001",
        "text": "USB 메모리를 사외로 반출할 때에는 정보보호팀의 사전 승인을 받아야 한다.",
        "tags": ["USB", "반출"]
      },
      {
        "blockId": "BLOCK-002",
        "text": "비밀번호는 8자리 이상, 영문/숫자/특수문자 조합으로 설정해야 한다.",
        "tags": ["비밀번호", "보안"]
      }
    ]
  }'
```

### 2차 응시 (중복 방지)

```bash
curl -X POST http://localhost:8000/ai/quiz/generate \
  -H "Content-Type: application/json" \
  -d '{
    "educationId": "EDU-SEC-2025-001",
    "docId": "DOC-SEC-001",
    "attemptNo": 2,
    "numQuestions": 5,
    "quizCandidateBlocks": [...],
    "excludePreviousQuestions": [
      {
        "questionId": "Q-20251212-ABCD1234",
        "stem": "USB 메모리를 사외로 반출할 때 필요한 조치는 무엇인가요?"
      }
    ]
  }'
```

## TODO: 향후 개선

1. **Phase 17**: LLM Self-check 기반 고급 QC 파이프라인
2. **문장 유사도**: Embedding 기반 중복 제거
3. **인증/권한**: IP 제한 또는 헤더 토큰 기반 인증
4. **캐싱**: 동일 블록 세트에 대한 결과 캐싱
5. **다중 정답 지원**: MCQ_MULTIPLE 문제 유형 추가

## 결론

Phase 16에서 퀴즈 자동 생성 API를 구현하여, 교육/사규 문서에서 객관식 퀴즈를 자동으로 생성할 수 있게 되었습니다. 1차/2차 응시 시나리오를 지원하며, 기본적인 정합성 검증(정답 1개, 옵션 수 등)을 수행합니다.
