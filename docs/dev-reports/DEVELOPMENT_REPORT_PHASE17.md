# Phase 17: 퀴즈 QC (Quality Check) 파이프라인 구현

## 개요

Phase 17에서는 **퀴즈 품질 검증(QC) 파이프라인**을 구현했습니다. LLM이 생성한 퀴즈 문항을 여러 단계로 검증하여, "완전히 이상한 퀴즈 문항"이 최종 결과에 포함되는 것을 방지합니다.

사람이 문항을 하나하나 검수하지 않아도 자동으로 품질을 보장하며, 검증 결과는 AI 로그에 저장되어 프롬프트 튜닝/모델 개선에 활용됩니다.

## 구현 내용

### 1. QC 파이프라인 3단계

```
┌─────────────────────────────────────────────────────────────┐
│                    QC 파이프라인                              │
├─────────────────────────────────────────────────────────────┤
│  1. SCHEMA 검증      →  2. SOURCE 검증      →  3. SELF_CHECK  │
│  (구조/정합성)           (원문 일치)             (LLM 검증)      │
└─────────────────────────────────────────────────────────────┘
```

| 단계 | 검증 내용 | 실패 예시 |
|------|----------|----------|
| **SCHEMA** | 필수 필드, 옵션 수, 정답 개수 | 정답이 없음, 보기가 1개뿐 |
| **SOURCE** | 정답이 출처 블록과 일치하는지 | 정답 키워드가 문서에 없음 |
| **SELF_CHECK** | LLM이 복수 정답/모호성 검증 | 보기 2개가 정답 가능 |

### 2. 파일 구조

```
app/
├── models/
│   └── quiz_qc.py           # QC DTO/Enum 정의
├── services/
│   ├── quiz_quality_service.py   # QC 파이프라인 서비스
│   └── quiz_generate_service.py  # QC 통합 (Phase 17 수정)
└── main.py

tests/
└── test_phase17_quiz_qc.py  # 25개 테스트
```

### 3. QC 결과 모델

#### QuizQcStage (실패 단계)

```python
class QuizQcStage(StrEnum):
    NONE = "NONE"            # 모든 검증 통과
    SCHEMA = "SCHEMA"        # 스키마/구조 검증에서 실패
    SOURCE = "SOURCE"        # 원문 일치 검증에서 실패
    SELF_CHECK = "SELF_CHECK"  # LLM Self-check에서 실패
```

#### QuizQcReasonCode (실패 사유)

```python
class QuizQcReasonCode(StrEnum):
    NONE = "NONE"                      # 실패 없음 (통과)
    INVALID_STRUCTURE = "INVALID_STRUCTURE"  # 필수 필드 누락, 옵션 부족
    MULTIPLE_CORRECT = "MULTIPLE_CORRECT"    # 정답 후보가 2개 이상
    NO_CORRECT_OPTION = "NO_CORRECT_OPTION"  # 정답이 없음
    SOURCE_MISMATCH = "SOURCE_MISMATCH"      # 문서와 상충
    LOW_QUALITY_TEXT = "LOW_QUALITY_TEXT"    # 너무 짧거나 의미 불명
    AMBIGUOUS_QUESTION = "AMBIGUOUS_QUESTION" # 질문이 모호함
    OTHER = "OTHER"                          # 기타 사유
```

#### QuizQuestionQcResult (문항별 QC 결과)

```json
{
  "questionId": "Q-20251212-ABCD1234",
  "qcPass": false,
  "qcStageFailed": "SELF_CHECK",
  "qcReasonCode": "MULTIPLE_CORRECT",
  "qcReasonDetail": "보기 1과 2 모두 문서에서 정답이 될 수 있습니다."
}
```

#### QuizSetQcResult (세트 요약)

```json
{
  "totalQuestions": 10,
  "passedQuestions": 8,
  "failedQuestions": 2,
  "questionResults": [...]
}
```

### 4. QuizQualityService 구현

```python
class QuizQualityService:
    async def validate_quiz_set(
        self,
        questions: List[GeneratedQuizQuestion],
        source_blocks: List[QuizCandidateBlock],
    ) -> Tuple[List[GeneratedQuizQuestion], QuizSetQcResult]:
        """
        입력: LLM이 생성한 퀴즈 문항 + 출처 블록
        출력: QC 통과한 문항 + 문항별 QC 결과
        """
        for question in questions:
            # 1. SCHEMA 검증
            schema_result = self._validate_schema(question)
            if not schema_result.qc_pass:
                continue

            # 2. SOURCE 검증
            source_result = self._validate_source(question, block_map)
            if not source_result.qc_pass:
                continue

            # 3. SELF_CHECK (LLM)
            selfcheck_result = await self._validate_selfcheck(question, block_map)
            if not selfcheck_result.qc_pass:
                continue

            valid_questions.append(question)

        return valid_questions, qc_summary
```

### 5. 각 단계별 검증 로직

#### (1) SCHEMA 검증

```python
def _validate_schema(self, question) -> QuizQuestionQcResult:
    # stem 존재 및 길이 검사 (최소 5자)
    if not question.stem or len(question.stem.strip()) < 5:
        return FAIL(LOW_QUALITY_TEXT)

    # 옵션 개수 검사 (최소 2개)
    if len(question.options) < 2:
        return FAIL(INVALID_STRUCTURE)

    # 정답 개수 검사 (정확히 1개)
    correct_count = sum(1 for opt in question.options if opt.is_correct)
    if correct_count == 0:
        return FAIL(NO_CORRECT_OPTION)
    if correct_count > 1:
        return FAIL(MULTIPLE_CORRECT)

    # 각 옵션 텍스트 검사
    for opt in question.options:
        if not opt.text or not opt.text.strip():
            return FAIL(LOW_QUALITY_TEXT)

    return PASS()
```

#### (2) SOURCE 검증

```python
def _validate_source(self, question, block_map) -> QuizQuestionQcResult:
    # 출처 블록 텍스트 수집
    source_texts = [block_map[id].text for id in question.source_block_ids]

    # 정답 텍스트에서 핵심 키워드 추출
    correct_option = get_correct_option(question)
    keywords = self._extract_keywords(correct_option.text)

    # 키워드가 출처에 포함되어 있는지 확인
    for keyword in keywords:
        if keyword in combined_source:
            return PASS()

    return FAIL(SOURCE_MISMATCH)
```

#### (3) SELF_CHECK (LLM)

```python
async def _validate_selfcheck(self, question, block_map) -> QuizQuestionQcResult:
    # LLM 프롬프트 구성
    messages = [
        {"role": "system", "content": SELF_CHECK_SYSTEM_PROMPT},
        {"role": "user", "content": format_question_for_check(question)},
    ]

    # LLM 호출
    llm_response = await self._llm.generate_chat_completion(messages)

    # 응답 파싱 {"verdict": "PASS/FAIL", "reason_code": "...", "reason_detail": "..."}
    result = parse_selfcheck_response(llm_response)

    if result.verdict == "PASS":
        return PASS()
    else:
        return FAIL(result.reason_code, result.reason_detail)
```

### 6. LLM Self-check 프롬프트

**System Prompt**:
```
당신은 기업 교육/사규 퀴즈의 품질을 검증하는 전문 검수자입니다.

품질 기준:
1. 정답이 정확히 1개여야 합니다 (복수 정답 불가)
2. 정답이 문서 내용과 일치해야 합니다
3. 오답 보기들이 명백히 틀린 내용이어야 합니다
4. 질문이 명확하고 모호하지 않아야 합니다
5. 문서만으로 답을 판단할 수 있어야 합니다

반드시 아래 JSON 형식으로만 응답하세요:
{
  "verdict": "PASS" 또는 "FAIL",
  "reason_code": "실패 시 사유 코드",
  "reason_detail": "상세 설명"
}
```

**User Prompt**:
```
## 문서 텍스트 (Source)
{source_text}

## 퀴즈 문항
**문제:** {stem}

**보기:**
1. 정보보호팀의 사전 승인 (정답)
2. 팀장에게 구두 보고
3. 자유롭게 반출
4. 사후 보고

위 문항을 검토하여 품질 기준을 충족하는지 JSON 형식으로 판단해 주세요.
```

### 7. QuizGenerateService 통합

```python
class QuizGenerateService:
    def __init__(self, qc_enabled: bool = True):
        self._qc_enabled = qc_enabled
        self._qc_service = None  # Lazy init
        self._last_qc_result = None

    async def generate_quiz(self, request) -> QuizGenerateResponse:
        # ... 기존 로직 (Phase 16) ...

        # 8. [Phase 17] QC 파이프라인 적용
        if self._qc_enabled and final_questions:
            final_questions, qc_result = await self._apply_qc_pipeline(
                questions=final_questions,
                source_blocks=request.quiz_candidate_blocks,
            )
            self._last_qc_result = qc_result

        return QuizGenerateResponse(
            questions=final_questions,
            generated_count=len(final_questions),
            ...
        )

    def get_last_qc_result(self) -> Optional[QuizSetQcResult]:
        """AI 로그 저장/분석용"""
        return self._last_qc_result
```

### 8. AI 로그용 메타 필드

```python
class QuizQcLogMeta(BaseModel):
    """프롬프트 튜닝/품질 분석에 활용"""

    education_id: str
    doc_id: str
    attempt_no: int
    quiz_qc_total_questions: int
    quiz_qc_passed_questions: int
    quiz_qc_failed_questions: int
    llm_prompt_version: str = "v1"
    llm_selfcheck_prompt_version: str = "v1"
```

## 테스트 결과

### Phase 17 테스트 (25개 통과)

```
TestQuizQcModels: 7개 - QC 모델 테스트
TestSchemaValidation: 4개 - SCHEMA 실패 케이스
TestSourceValidation: 2개 - SOURCE 실패/통과 케이스
TestSelfCheckValidation: 4개 - SELF_CHECK 실패/통과 케이스
TestAllStagesPass: 1개 - 모든 단계 통과 케이스
TestSetSummary: 1개 - 세트 요약 결과 검증
TestQuizGenerateServiceIntegration: 3개 - 서비스 통합 테스트
TestHelperFunctions: 3개 - 헬퍼 함수 테스트
```

### 전체 테스트

```
============================= 360 passed in 7.42s =============================
```

## 변경된 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/models/quiz_qc.py` | 신규 - QC DTO/Enum 모델 |
| `app/services/quiz_quality_service.py` | 신규 - QC 파이프라인 서비스 |
| `app/services/quiz_generate_service.py` | 수정 - QC 통합 |
| `tests/test_phase17_quiz_qc.py` | 신규 - 25개 테스트 |
| `tests/test_phase16_quiz_generate.py` | 수정 - QC 비활성화 옵션 추가 |

## 사용 예시

### QC 활성화 (기본값)

```python
from app.services.quiz_generate_service import QuizGenerateService

service = QuizGenerateService()  # qc_enabled=True (기본값)
response = await service.generate_quiz(request)

# QC 결과 확인 (AI 로그용)
qc_result = service.get_last_qc_result()
print(f"통과: {qc_result.passed_questions}/{qc_result.total_questions}")

for qr in qc_result.question_results:
    if not qr.qc_pass:
        print(f"  - {qr.question_id}: {qr.qc_stage_failed} / {qr.qc_reason_code}")
```

### QC 비활성화 (비용 절감)

```python
# Self-check LLM 호출 비용이 부담될 경우
service = QuizGenerateService(qc_enabled=False)
response = await service.generate_quiz(request)
```

### Self-check만 비활성화

```python
from app.services.quiz_quality_service import QuizQualityService

# SCHEMA + SOURCE 검증만 수행, LLM Self-check 생략
qc_service = QuizQualityService(selfcheck_enabled=False)
valid_questions, qc_result = await qc_service.validate_quiz_set(
    questions=generated_questions,
    source_blocks=quiz_candidate_blocks,
)
```

## 정책 결정 사항

### 1. 문항이 0개가 되는 경우

**정책 A (현재 적용)**: 빈 리스트 반환
- 백엔드/프론트에서 "이번에는 문제가 생성되지 않았다" 메시지 표시
- HTTP 200 OK 응답

```python
# 문항이 0개여도 빈 리스트 반환 (정책 A)
# 백엔드/프론트에서 "이번에는 문제가 생성되지 않았다" 처리
return QuizGenerateResponse(generated_count=0, questions=[])
```

### 2. Self-check LLM 에러 시

**정책**: FAIL 처리 (보수적 접근)
- LLM 호출 실패 시 해당 문항은 통과시키지 않음
- 향후 Skip으로 변경 가능

```python
except Exception as e:
    # 에러 시 FAIL 처리 (보수적 접근)
    return QuizQuestionQcResult(
        qc_pass=False,
        qc_stage_failed=QuizQcStage.SELF_CHECK,
        qc_reason_code=QuizQcReasonCode.OTHER,
        qc_reason_detail=f"Self-check LLM 호출 실패: {str(e)}",
    )
```

## TODO: 향후 개선

1. **RAG/Embedding 기반 SOURCE 검증**: 현재는 키워드 기반, 향후 의미 유사도 검증 추가
2. **샘플링 비율**: Self-check 비용 절감을 위해 일부 문항만 검사하는 옵션
3. **QC 결과 API**: 별도 엔드포인트로 QC 결과 조회 기능
4. **대시보드 연동**: ctrlf-back 관리자 대시보드에서 QC 통계 시각화
5. **프롬프트 버전 관리**: Self-check 프롬프트 튜닝 시 버전별 성능 비교

## 결론

Phase 17에서 퀴즈 QC 파이프라인을 구현하여, LLM이 생성한 문항의 품질을 자동으로 검증할 수 있게 되었습니다. 3단계 검증(SCHEMA → SOURCE → SELF_CHECK)을 통해 이상한 퀴즈 문항이 최종 결과에 포함되는 것을 방지하며, QC 결과는 프롬프트 튜닝/품질 분석에 활용됩니다.
