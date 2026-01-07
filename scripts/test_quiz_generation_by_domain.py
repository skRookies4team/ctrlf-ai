import pytest
import sys
import asyncio
import os
import json
from pathlib import Path
from typing import Any, Optional, List, Set

# ============================================================
# Path 설정
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# ============================================================
# LLM BASE URL 강제 적용 (REAL)
# ============================================================
if "LLM_BASE_URL_REAL" in os.environ:
    os.environ["LLM_BASE_URL"] = os.environ["LLM_BASE_URL_REAL"]
    print(f"✅ Using LLM_BASE_URL_REAL → {os.environ['LLM_BASE_URL']}")

# ============================================================
# Imports
# ============================================================
from app.repositories.milvus_quiz_repository import MilvusQuizRepository
from app.services.quiz_generate_service import QuizGenerateService
from app.services.domain_quiz_batch_service import DomainQuizBatchService
from app.clients.milvus_client import get_milvus_client


# ============================================================
# Dataset 정의
# ============================================================
ALL_DATASET_IDS = [
    "직무교육",
    "장애인인식개선교육",
    "직장내괴롭힘교육",
    "직장내성희롱교육",
    "정보보안교육",
    "사내규정",
]

DEFAULT_DATASET_ID = "장애인인식개선교육"


def pick_dataset_id_from_argv(default: str = DEFAULT_DATASET_ID) -> str:
    """
    사용:
      python scripts/test_quiz_generation_by_domain.py 직장내성희롱교육
    """
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        return sys.argv[1].strip()
    return default


# ============================================================
# Milvus 연결 보장
# ============================================================
async def ensure_milvus_ready():
    client = get_milvus_client()
    await client._ensure_connection()
    await client._get_collection_async()


@pytest.fixture(autouse=True)
async def milvus_connection():
    await ensure_milvus_ready()
    yield


def print_qc_debug(quiz_service: QuizGenerateService):
    qc = quiz_service.get_last_qc_result()
    if not qc:
        print("\n[QC DEBUG] qc_result is None")
        return

    print(
        f"\n[QC DEBUG] total={qc.total_questions}, passed={qc.passed_questions}, failed={qc.failed_questions}"
    )

    failed = [r for r in qc.question_results if not r.qc_pass]
    for i, r in enumerate(failed[:5], 1):
        print(
            f"[QC FAIL #{i}] stage={r.qc_stage_failed} "
            f"code={r.qc_reason_code} detail={r.qc_reason_detail}"
        )


# ============================================================
# 로컬(스크립트) 중복 검사 유틸
#  - 실제 embedding 중복 제거는 QuizGenerateService에서 수행하는 게 맞음
#  - 여기서는 스모크테스트로 "너무 비슷한 stem"이 섞였는지 잡는 용도
# ============================================================
def _normalize_for_dup(text: str) -> List[str]:
    text = (text or "").lower()
    # 한글/영문/숫자만 남기고 나머지 공백 처리
    cleaned = []
    for ch in text:
        if ch.isalnum() or ("\uac00" <= ch <= "\ud7a3"):
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    tokens = "".join(cleaned).split()
    return tokens


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def assert_no_near_duplicates(stems: List[str], threshold: float = 0.85):
    """
    stem 토큰 Jaccard로 "거의 같은 문제"가 섞이면 테스트에서 잡는다.
    threshold=0.85 정도면 문장만 살짝 바뀐 중복은 대부분 걸림.
    """
    token_sets = [set(_normalize_for_dup(s)) for s in stems]
    n = len(stems)
    for i in range(n):
        for j in range(i + 1, n):
            sim = _jaccard(token_sets[i], token_sets[j])
            if sim >= threshold:
                raise AssertionError(
                    f"Near-duplicate stems detected (Jaccard={sim:.2f} >= {threshold})\n"
                    f"- A: {stems[i]}\n"
                    f"- B: {stems[j]}"
                )


# ============================================================
# Backend 전달용 Payload 변환
# ============================================================
def build_backend_payload(
    dataset_id: str,
    response: Any,
    quiz_service: QuizGenerateService,
    requested_count: int,
):
    qc_result = quiz_service.get_last_qc_result()

    questions_payload = []
    for q in getattr(response, "questions", []) or []:
        source_ids = getattr(q, "source_block_ids", []) or []

        options_payload = []
        answer_index: Optional[int] = None
        for idx, opt in enumerate(q.options):
            options_payload.append(
                {
                    "text": opt.text,
                    "is_correct": bool(opt.is_correct),
                }
            )
            if opt.is_correct:
                answer_index = idx

        questions_payload.append(
            {
                "question_id": getattr(q, "question_id", None),
                "stem": q.stem,
                "options": options_payload,
                "answer_index": answer_index,
                "difficulty": getattr(q, "difficulty", None),
                "source_block_ids": source_ids,
            }
        )

    passed = qc_result.passed_questions if qc_result else 0
    failed = qc_result.failed_questions if qc_result else (requested_count - passed)

    payload = {
        "dataset_id": dataset_id,
        "requested_count": requested_count,
        "generated_count": len(questions_payload),
        "qc": {
            "passed": passed,
            "failed": failed,
        },
        "questions": questions_payload,
    }

    return payload


# ============================================================
# 공용 실행 함수
# ============================================================
async def run_quiz_generation(
    dataset_id: str,
    num_questions: int = 20,
):
    await ensure_milvus_ready()

    milvus_repo = MilvusQuizRepository(collection_name="ragflow_chunks_openai")

    qc_enabled = os.getenv("QUIZ_QC_ENABLED", "true").lower() == "true"
    quiz_service = QuizGenerateService(qc_enabled=qc_enabled)

    batch_service = DomainQuizBatchService(
        milvus_repo=milvus_repo,
        quiz_service=quiz_service,
    )

    response = await batch_service.generate_domain_quiz(
        dataset_id=dataset_id,
        num_questions=num_questions,
    )

    payload = build_backend_payload(
        dataset_id=dataset_id,
        response=response,
        quiz_service=quiz_service,
        requested_count=num_questions,
    )

    return response, quiz_service, payload


# ============================================================
# Pytest Smoke Test
# ============================================================
@pytest.mark.asyncio
async def test_generate_20_quizzes():
    dataset_id = DEFAULT_DATASET_ID

    response, quiz_service, payload = await run_quiz_generation(
        dataset_id=dataset_id,
        num_questions=20,
    )

    assert payload["requested_count"] == 20
    assert payload["generated_count"] >= 0

    stems = []
    for q in payload["questions"]:
        assert q["stem"]
        stems.append(q["stem"])

        assert len(q["options"]) == 4
        assert q["answer_index"] is not None

        # ✅ 2) source_block 자동 매핑이 되면 "빈 리스트면 안 됨"
        #    (만약 아직 구현 전이면 이 assert를 잠시 주석 처리)
        assert isinstance(q["source_block_ids"], list)
        assert len(q["source_block_ids"]) >= 1, f"source_block_ids is empty for stem={q['stem']}"

    # ✅ 1) 중복 제거가 제대로면, "거의 같은 stem"이 있으면 안 됨
    if len(stems) >= 2:
        assert_no_near_duplicates(stems, threshold=0.85)

    # QC ON이면 결과가 있어야 함
    if os.getenv("QUIZ_QC_ENABLED", "true").lower() == "true":
        assert quiz_service.get_last_qc_result() is not None


# ============================================================
# 단독 실행
# ============================================================
if __name__ == "__main__":
    dataset_id = pick_dataset_id_from_argv(DEFAULT_DATASET_ID)
    print("🚀 Domain Quiz Generation (Manual Run)")
    print(f"▶ Dataset: {dataset_id}")

    response, quiz_service, payload = asyncio.run(
        run_quiz_generation(
            dataset_id=dataset_id,
            num_questions=20,
        )
    )

    print(f"\n✅ Generated quizzes: {payload['generated_count']}")

    if payload["generated_count"] == 0:
        print_qc_debug(quiz_service)

    qc = quiz_service.get_last_qc_result()
    if qc and payload["generated_count"] == 0:
        print("\n🧨 QC Debug (Top 5):")
        for r in (qc.question_results or [])[:5]:
            print(
                f"- id={r.question_id} pass={r.qc_pass} "
                f"stage={r.qc_stage_failed} reason={r.qc_reason_code} detail={r.qc_reason_detail}"
            )

    # ✅ 실행 모드에서도 "중복/소스 비어있음" 빠르게 체크
    stems = [q["stem"] for q in payload.get("questions", []) if q.get("stem")]
    if len(stems) >= 2:
        try:
            assert_no_near_duplicates(stems, threshold=0.85)
        except AssertionError as e:
            print("\n⚠️ Near-duplicate detected in manual run:")
            print(str(e))

    empty_source = [q for q in payload.get("questions", []) if not q.get("source_block_ids")]
    if empty_source:
        print(f"\n⚠️ source_block_ids empty: {len(empty_source)} questions")
        print(f"  e.g. {empty_source[0].get('stem')}")

    print("\n📦 Backend Payload Preview:\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
