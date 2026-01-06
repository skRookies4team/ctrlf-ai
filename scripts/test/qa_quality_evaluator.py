"""
LLM-as-a-Judge 챗봇 응답 품질 자동 평가 스크립트

배치 테스트 결과(질답리스트_EXAONE_*.xlsx)를 입력받아
GPT-4o로 각 응답의 품질을 자동 평가합니다.

사용법:
    python qa_quality_evaluator.py                     # 최신 결과 파일 자동 선택
    python qa_quality_evaluator.py -f 결과파일.xlsx     # 특정 파일 평가
    python qa_quality_evaluator.py -n 30               # 30개만 샘플링 평가
    python qa_quality_evaluator.py --model gpt-4o-mini # 저렴한 모델 사용

평가 기준:
    - 정확성 (Accuracy): 사실적으로 정확한가? (1-5)
    - 완전성 (Completeness): 질문에 충분히 답했는가? (1-5)
    - 관련성 (Relevance): 질문과 관련된 답변인가? (1-5)
    - 환각 (Hallucination): 거짓 정보가 있는가? (Y/N)
    - 종합 점수 (Overall): 전체 품질 (1-5)
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from openai import AsyncOpenAI

# 설정
EVAL_MODEL = "gpt-4o"  # 평가용 모델 (gpt-4o-mini도 가능)
MAX_CONCURRENT = 5     # 동시 API 요청 수
TIMEOUT = 60           # API 타임아웃
OUTPUT_DIR = Path(__file__).parent / "docs"

# 평가 프롬프트
EVAL_SYSTEM_PROMPT = """당신은 AI 챗봇 응답 품질을 평가하는 전문가입니다.
사용자 질문과 챗봇의 답변을 분석하여 품질을 평가해주세요.

반드시 아래 JSON 형식으로만 응답하세요:
{
    "accuracy": <1-5>,
    "completeness": <1-5>,
    "relevance": <1-5>,
    "hallucination": "<Y|N>",
    "overall": <1-5>,
    "feedback": "<한국어로 50자 이내 간단한 피드백>"
}

평가 기준:
- accuracy (정확성): 답변이 사실적으로 정확한가? (1=매우 부정확, 5=매우 정확)
- completeness (완전성): 질문에 충분히 답했는가? (1=매우 불충분, 5=완벽히 충분)
- relevance (관련성): 질문과 관련된 답변인가? (1=전혀 무관, 5=매우 관련)
- hallucination (환각): 사실이 아닌 정보를 지어냈는가? (Y=환각있음, N=환각없음)
- overall (종합): 전체적인 답변 품질 (1=매우 나쁨, 5=매우 좋음)
- feedback: 개선점이나 특이사항

참고:
- "모르겠습니다", "정보가 없습니다" 등의 정직한 불확실성 표현은 환각이 아님
- 회사 정책/규정 질문에는 정확한 정보 제공이 중요
- RAG 기반 답변이므로 출처 없이 지어낸 정보는 환각으로 판정"""


EVAL_USER_PROMPT_TEMPLATE = """다음 질문과 답변의 품질을 평가해주세요.

[질문]
{question}

[챗봇 답변]
{answer}

[도메인]
{domain}

[RAG 출처 사용 여부]
{rag_used}

위 내용을 바탕으로 JSON 형식으로 평가 결과를 제공해주세요."""


async def evaluate_single(
    client: AsyncOpenAI,
    row: dict,
    semaphore: asyncio.Semaphore,
    model: str
) -> dict:
    """단일 응답 품질 평가"""
    async with semaphore:
        result = {
            "ID": row.get("ID", ""),
            "질문": row.get("질문", ""),
            "답변": row.get("답변", "")[:500],  # 미리보기용 축약
            "정확성": 0,
            "완전성": 0,
            "관련성": 0,
            "환각여부": "",
            "종합점수": 0,
            "피드백": "",
            "평가오류": ""
        }

        question = row.get("질문", "")
        answer = row.get("답변", "")
        domain = row.get("domain", row.get("도메인", "N/A"))
        rag_used = row.get("rag_used", row.get("RAG_사용", "N/A"))

        # 답변이 없거나 에러인 경우
        if not answer or row.get("error") or row.get("에러"):
            result["평가오류"] = "답변 없음 또는 에러"
            return result

        user_prompt = EVAL_USER_PROMPT_TEMPLATE.format(
            question=question,
            answer=answer[:2000],  # 토큰 제한
            domain=domain,
            rag_used=rag_used
        )

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": EVAL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=300,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content
            eval_result = json.loads(content)

            result["정확성"] = eval_result.get("accuracy", 0)
            result["완전성"] = eval_result.get("completeness", 0)
            result["관련성"] = eval_result.get("relevance", 0)
            result["환각여부"] = eval_result.get("hallucination", "")
            result["종합점수"] = eval_result.get("overall", 0)
            result["피드백"] = eval_result.get("feedback", "")

        except json.JSONDecodeError as e:
            result["평가오류"] = f"JSON 파싱 실패: {str(e)[:50]}"
        except Exception as e:
            result["평가오류"] = str(e)[:100]

        return result


async def evaluate_batch(
    df: pd.DataFrame,
    model: str,
    progress_callback=None
) -> list:
    """배치 평가 실행"""
    client = AsyncOpenAI()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results = []

    tasks = []
    for _, row in df.iterrows():
        task = evaluate_single(client, row.to_dict(), semaphore, model)
        tasks.append(task)

    completed = 0
    total = len(tasks)

    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
        completed += 1

        if progress_callback:
            progress_callback(completed, total, result)

    return results


def print_progress(completed: int, total: int, result: dict):
    """진행 상황 출력"""
    pct = (completed / total) * 100
    score = result.get("종합점수", "-")
    status = f"점수:{score}" if not result["평가오류"] else f"오류:{result['평가오류'][:20]}"
    print(f"\r[{completed}/{total}] ({pct:.1f}%) {result['ID']}: {status}".ljust(80), end="", flush=True)


def find_latest_result_file() -> Path:
    """최신 배치 테스트 결과 파일 찾기"""
    docs_dir = OUTPUT_DIR
    pattern = "질답리스트_EXAONE*.xlsx"
    files = list(docs_dir.glob(pattern))

    if not files:
        # 다른 패턴도 시도
        pattern2 = "질답리스트*.xlsx"
        files = list(docs_dir.glob(pattern2))

    if not files:
        raise FileNotFoundError(f"결과 파일을 찾을 수 없습니다: {docs_dir}/{pattern}")

    # 최신 파일 반환 (수정 시간 기준)
    return max(files, key=lambda f: f.stat().st_mtime)


def parse_args():
    parser = argparse.ArgumentParser(
        description="LLM-as-a-Judge 챗봇 응답 품질 평가",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-f", "--file",
        type=str,
        default=None,
        help="평가할 결과 파일 경로 (미지정 시 최신 파일 자동 선택)"
    )
    parser.add_argument(
        "-n", "--sample",
        type=int,
        default=None,
        help="평가할 샘플 수 (미지정 시 전체 평가)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=EVAL_MODEL,
        help=f"평가용 모델 (기본값: {EVAL_MODEL})"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="랜덤 시드 (샘플링 시)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("LLM-as-a-Judge 챗봇 응답 품질 평가")
    print("=" * 60)

    # OpenAI API 키 확인
    if not os.getenv("OPENAI_API_KEY"):
        print("[오류] OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    # 입력 파일 결정
    if args.file:
        input_file = Path(args.file)
    else:
        try:
            input_file = find_latest_result_file()
        except FileNotFoundError as e:
            print(f"[오류] {e}")
            print("\n먼저 배치 테스트를 실행해주세요:")
            print("  python qa_batch_test.py -n 50")
            sys.exit(1)

    print(f"\n1. 입력 파일: {input_file}")
    print(f"   평가 모델: {args.model}")

    # 데이터 로드
    df = pd.read_excel(input_file, engine="openpyxl")
    total_count = len(df)
    print(f"   총 {total_count}개 레코드 로드됨")

    # 에러가 아닌 레코드만 필터링
    error_col = "error" if "error" in df.columns else "에러"
    if error_col in df.columns:
        df_valid = df[df[error_col].isna() | (df[error_col] == "")]
        print(f"   유효 레코드: {len(df_valid)}개 (에러 제외)")
    else:
        df_valid = df

    # 샘플링
    if args.sample and args.sample < len(df_valid):
        import random
        random.seed(args.seed)
        indices = random.sample(range(len(df_valid)), args.sample)
        df_valid = df_valid.iloc[indices]
        print(f"   {args.sample}개 랜덤 샘플링 (seed={args.seed})")

    # 평가 실행
    print(f"\n2. 품질 평가 시작 (동시 요청: {MAX_CONCURRENT}개)")
    print("-" * 60)

    start_time = time.time()
    results = asyncio.run(evaluate_batch(df_valid, args.model, print_progress))
    elapsed = time.time() - start_time

    print(f"\n\n3. 평가 완료!")
    print(f"   소요 시간: {elapsed:.1f}초")

    # 결과 정리
    results_df = pd.DataFrame(results)

    # ID로 정렬
    if "ID" in results_df.columns:
        results_df = results_df.sort_values("ID").reset_index(drop=True)

    # 통계 계산
    valid_results = results_df[results_df["평가오류"] == ""]
    if len(valid_results) > 0:
        avg_accuracy = valid_results["정확성"].mean()
        avg_completeness = valid_results["완전성"].mean()
        avg_relevance = valid_results["관련성"].mean()
        avg_overall = valid_results["종합점수"].mean()
        hallucination_rate = (valid_results["환각여부"] == "Y").sum() / len(valid_results) * 100
    else:
        avg_accuracy = avg_completeness = avg_relevance = avg_overall = 0
        hallucination_rate = 0

    # 결과 저장
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sample_suffix = f"_n{len(results)}" if args.sample else ""
    output_file = OUTPUT_DIR / f"품질평가결과{sample_suffix}_{timestamp}.xlsx"

    print(f"\n4. 결과 저장: {output_file}")

    # 요약 시트와 상세 시트 저장
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        # 상세 결과
        results_df.to_excel(writer, sheet_name="상세결과", index=False)

        # 요약 통계
        summary_data = {
            "항목": ["평가 모델", "총 평가 수", "성공", "실패",
                    "평균 정확성", "평균 완전성", "평균 관련성", "평균 종합점수",
                    "환각 비율(%)"],
            "값": [args.model, len(results), len(valid_results),
                   len(results) - len(valid_results),
                   f"{avg_accuracy:.2f}", f"{avg_completeness:.2f}",
                   f"{avg_relevance:.2f}", f"{avg_overall:.2f}",
                   f"{hallucination_rate:.1f}%"]
        }
        summary_df = pd.DataFrame(summary_data)
        summary_df.to_excel(writer, sheet_name="요약", index=False)

        # 점수별 분포
        if len(valid_results) > 0:
            dist_data = {
                "종합점수": [1, 2, 3, 4, 5],
                "개수": [
                    (valid_results["종합점수"] == i).sum()
                    for i in range(1, 6)
                ]
            }
            dist_df = pd.DataFrame(dist_data)
            dist_df.to_excel(writer, sheet_name="점수분포", index=False)

    print("   저장 완료!")

    # 콘솔 요약 출력
    print("\n" + "=" * 60)
    print("품질 평가 요약")
    print("=" * 60)
    print(f"평가 모델: {args.model}")
    print(f"총 평가 수: {len(results)}")
    print(f"성공: {len(valid_results)}, 실패: {len(results) - len(valid_results)}")
    print("-" * 40)
    print(f"평균 정확성:    {avg_accuracy:.2f} / 5.00")
    print(f"평균 완전성:    {avg_completeness:.2f} / 5.00")
    print(f"평균 관련성:    {avg_relevance:.2f} / 5.00")
    print(f"평균 종합점수:  {avg_overall:.2f} / 5.00")
    print(f"환각 비율:      {hallucination_rate:.1f}%")
    print("=" * 60)

    # 점수별 분포
    if len(valid_results) > 0:
        print("\n종합점수 분포:")
        for score in range(1, 6):
            count = (valid_results["종합점수"] == score).sum()
            pct = count / len(valid_results) * 100
            bar = "#" * int(pct / 5)
            print(f"  {score}점: {bar} {count}개 ({pct:.1f}%)")

    # 환각 있는 답변 샘플 출력
    hallucinated = valid_results[valid_results["환각여부"] == "Y"]
    if len(hallucinated) > 0:
        print(f"\n환각 탐지된 답변 ({len(hallucinated)}개):")
        for _, row in hallucinated.head(3).iterrows():
            print(f"  - ID {row['ID']}: {row['질문'][:40]}...")
            print(f"    피드백: {row['피드백']}")

    # 저품질 답변 샘플 출력
    low_quality = valid_results[valid_results["종합점수"] <= 2]
    if len(low_quality) > 0:
        print(f"\n저품질 답변 (2점 이하, {len(low_quality)}개):")
        for _, row in low_quality.head(3).iterrows():
            print(f"  - ID {row['ID']}: {row['질문'][:40]}...")
            print(f"    피드백: {row['피드백']}")

    print(f"\n결과 파일: {output_file}")
    return output_file


if __name__ == "__main__":
    output = main()
    print(f"\n완료: {output}")
