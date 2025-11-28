import json
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from openai import OpenAI
from dotenv import load_dotenv
import os
import gc
import time
import datetime
from pathlib import Path
from ai_llm_model_test.rag.generator import generate_rag_answer

# === .env 파일 로드 ===
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
EVAL_FILE = str((BASE_DIR.parent / "rag" / "emp_eval_30.json"))
# RAG 평가 사용 여부 (기본 True). 환경변수 EVAL_USE_RAG=false 로 끌 수 있음.
USE_RAG = os.getenv("EVAL_USE_RAG", "true").lower() in ("1", "true", "yes")

MODELS = {
    "Qwen2-7B-Instruct": "/home/team4/.cache/huggingface/hub/models--Qwen--Qwen2-7B-Instruct/snapshots/f2826a00ceef68f0f2b946d945ecc0477ce4450c",
    "Llama3-8B-Instruct": "/home/team4/.cache/huggingface/hub/models--meta-llama--Meta-Llama-3-8B-Instruct/snapshots/8afb486c1db24fe5011ec46dfbe5b5dccdb575c2",
    "Gemma-3-12B-Instruct": "/home/team4/.cache/huggingface/hub/models--google--gemma-3-12b-it/snapshots/96b6f1eccf38110c56df3a15bffe176da04bfd80"
}

# === GPT-4o 클라이언트 ===
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# =====================================================
# 파일 로드 함수
# =====================================================
def load_data():
    with open(EVAL_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# =====================================================
# 모델 로드 함수 (8bit는 BitsAndBytesConfig 사용)
# =====================================================
def load_model(model_path, use_8bit=False):
    if use_8bit:
        quant_config = BitsAndBytesConfig(load_in_8bit=True)
        return AutoModelForCausalLM.from_pretrained(
            model_path,
            quantization_config=quant_config,
            device_map="auto",
            trust_remote_code=True
        )

    return AutoModelForCausalLM.from_pretrained(
        model_path,
        dtype=torch.float16,
        device_map={"": "cuda:0"},
        trust_remote_code=True
    )

# =====================================================
# GPT-4o judge: 의미 일치 기반 평가
# =====================================================
def llm_judge_score(question, expected, answer):
    prompt = f"""
너는 LLM 성능 평가를 수행하는 전문 채점자(Judge)이다.  
항상 **한국어로 평가**하며, 모델 답변이 정답과 의미적으로 얼마나 일치하는지 채점하라.

### 질문
{question}

### 정답
{expected}

### 모델의 답변
{answer}

### 채점 기준 (0~1)
- 0.9~1.0 : 의미 정확도 매우 높음
- 0.7~0.89 : 핵심 내용 대부분 일치
- 0.4~0.69 : 절반 정도만 일치
- 0.1~0.39 : 일부만 일치
- 0.0~0.09 : 거의 또는 전혀 일치하지 않음

출력은 JSON 형식으로 한 줄만:
{{"score": float}}
"""

    # 1차 요청: JSON 강제
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        return json.loads(raw)["score"]
    except Exception:
        pass

    # 2차 재시도: 포맷 유연, 실패 시 제외(None)
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        raw = response.choices[0].message.content
        print("\n🔍 DEBUG RAW RESPONSE (retry):", raw)
        return json.loads(raw)["score"]
    except Exception:
        print("❌ GPT-4o 채점 JSON 파싱 실패: 해당 항목은 평균 계산에서 제외합니다.")
        return None

# =====================================================
# 결과 저장 함수
# =====================================================
def save_results(model_name, results, avg_score):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{model_name}_eval_result_{timestamp}.json"

    data = {
        "model": model_name,
        "average_score": avg_score,
        "results": results
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"📁 결과 저장 완료: {filename}")

# =====================================================
# 모델 평가 함수
# =====================================================
def evaluate_model(model_name, model_path, eval_data, use_8bit=False):
    print(f"\n===== Evaluating {model_name} =====")

    if not USE_RAG:
        tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        tokenizer.pad_token = tokenizer.eos_token
        model = load_model(model_path, use_8bit=use_8bit)
        model.eval()

    scores = []
    results = []

    for item in eval_data:
        question = item["question"]
        expected = item["expected_answer"]

        if USE_RAG:
            rag = generate_rag_answer(
                question=question,
                model_path=model_path,
                use_8bit=use_8bit,
                top_k=5
            )
            answer = rag["answer"]
        else:
            # 🔥 질문 앞에 "가능하면 한국어로 답해주세요" 추가
            prompt = f"가능하면 한국어로 답변해줘.\n\n{question}"
            inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
            output = model.generate(
                **inputs,
                max_new_tokens=150,
                temperature=0.2
            )
            answer = tokenizer.decode(output[0], skip_special_tokens=True)

        score = llm_judge_score(question, expected, answer)

        print(f"\nQ: {question}")
        print(f"Model Answer : {answer}")
        print(f"Expected     : {expected}")
        print(f"Judge Score  : {0.0 if score is None else score:.3f}")

        if score is not None:
            scores.append(score)
        results.append({
            "question": question,
            "expected": expected,
            "answer": answer,
            "score": 0.0 if score is None else score
        })

        time.sleep(0.3)

    avg_score = (sum(scores) / len(scores)) if len(scores) > 0 else 0.0
    print(f"\n🔥 {model_name} FINAL SCORE: {avg_score:.3f}")

    save_results(model_name, results, avg_score)

    if not USE_RAG:
        del model
        del tokenizer
        torch.cuda.empty_cache()
        gc.collect()

    return avg_score

# =====================================================
# 🔥 Main 실행부
# =====================================================
if __name__ == "__main__":
    eval_data = load_data()
    # 평가 문항을 20개로 제한
    eval_data = eval_data[:20]

    gemma_score = evaluate_model(
        "Gemma-3-12B-Instruct",
        MODELS["Gemma-3-12B-Instruct"],
        eval_data,
        use_8bit=True
    )

    qwen_score = evaluate_model(
        "Qwen2-7B-Instruct",
        MODELS["Qwen2-7B-Instruct"],
        eval_data,
        use_8bit=True
    )

    llama_score = evaluate_model(
        "Llama3-8B-Instruct",
        MODELS["Llama3-8B-Instruct"],
        eval_data,
        use_8bit=True
    )

    print("\n===== FINAL RESULT =====")
    print(f"Qwen2 7B Score : {qwen_score:.3f}")
    print(f"Llama3 8B Score: {llama_score:.3f}")
    print(f"Gemma3 12B Score: {gemma_score:.3f}")

    scores = {
        "Qwen2-7B-Instruct": qwen_score,
        "Llama3-8B-Instruct": llama_score,
        "Gemma-3-12B-Instruct": gemma_score,
    }
    winner = max(scores, key=scores.get)
    print(f"🏆 Winner: {winner}")
