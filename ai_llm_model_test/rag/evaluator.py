import os
import json
import time
import gc
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from transformers import AutoTokenizer, AutoModelForCausalLM
import torch
 
from .generator import generate_rag_answer
 
 
load_dotenv()
client = OpenAI()  # OPENAI_API_KEY 자동 로드
 
BASE_DIR = Path(__file__).parent
EVAL_FILE = str(BASE_DIR / "emp_eval_50.json")
 
# 환경변수 우선, 없으면 공개 모델 ID 사용
BASE_MODEL_PATH = os.getenv("LOCAL_LLM_PATH", "Qwen/Qwen2-7B-Instruct")
RAG_MODEL_PATH = BASE_MODEL_PATH  # 같은 모델 사용(RAG는 문서 검색만 추가)


# ------------------------------------------------------
# GPT-4o Judge
# ------------------------------------------------------
def gpt_judge(question, expected, answer):
    prompt = f"""
당신은 공정한 LLM 평가자(Judge)입니다.

### 질문
{question}

### 정답(정답 기준)
{expected}

### 모델 답변
{answer}

0~1 점수로 평가하되 다음 기준을 지키세요:
- 의미가 거의 같으면 0.9~1.0
- 핵심 내용 대부분 맞으면 0.7~0.89
- 절반 정도 일치하면 0.4~0.69
- 일부만 맞으면 0.1~0.39
- 거의 다 틀리면 0.0~0.09

출력은 반드시 JSON 한 줄만:
{{"score": float}}
"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    raw = response.choices[0].message.content
    try:
        return json.loads(raw)["score"]
    except:
        print("⚠️ GPT-4o Judge JSON 파싱 실패:", raw)
        return 0.0


# ------------------------------------------------------
# Base Model Generation
# ------------------------------------------------------
def load_base_model():
    """
    로컬 LLM 로딩 (base 모델).
    GPU 및 8bit 가능 시 8bit → 그 외 fp16/cpu fp32로 폴백.
    """
    prefer_8bit = bool(torch.cuda.is_available())
    try:
        if prefer_8bit:
            model = AutoModelForCausalLM.from_pretrained(
                BASE_MODEL_PATH,
                load_in_8bit=True,
                device_map="auto",
                trust_remote_code=True,
            )
        else:
            dtype = torch.float16 if torch.cuda.is_available() else torch.float32
            model = AutoModelForCausalLM.from_pretrained(
                BASE_MODEL_PATH,
                torch_dtype=dtype,
                device_map="auto",
                trust_remote_code=True,
            )
    except Exception:
        # 최후 폴백: CPU float32
        model = AutoModelForCausalLM.from_pretrained(
            BASE_MODEL_PATH,
            torch_dtype=torch.float32,
            device_map="cpu",
            trust_remote_code=True,
        )
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_PATH, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token
    return model, tokenizer


def generate_base_answer(question, model, tokenizer):
    inputs = tokenizer(question, return_tensors="pt").to(model.device)
    output = model.generate(
        **inputs,
        max_new_tokens=150,
        temperature=0.2
    )
    return tokenizer.decode(output[0], skip_special_tokens=True)


# ------------------------------------------------------
# Evaluation Pipeline
# ------------------------------------------------------
def evaluate():
    print("\n🚀 Starting RAG vs Base Model Evaluation...")
    with open(EVAL_FILE, "r", encoding="utf-8") as f:
        eval_data = json.load(f)

    base_model, base_tokenizer = load_base_model()

    results = []

    for item in eval_data:
        q = item["question"]
        expected = item["expected_answer"]
        qid = item["id"]

        print(f"\n==============================")
        print(f"🔍 ID: {qid}")
        print(f"❓ Question: {q}")

        # ---- Base Model ----
        base_answer = generate_base_answer(q, base_model, base_tokenizer)
        base_score = gpt_judge(q, expected, base_answer)

        print(f"\n📌 Base Answer: {base_answer}")
        print(f"📊 Base Score: {base_score:.3f}")

        # ---- RAG Model ----
        rag_output = generate_rag_answer(q, RAG_MODEL_PATH, use_8bit=True)
        rag_answer = rag_output["answer"]
        rag_score = gpt_judge(q, expected, rag_answer)

        print(f"\n📌 RAG Answer: {rag_answer}")
        print(f"📊 RAG Score: {rag_score:.3f}")

        # ---- Save ----
        results.append({
            "id": qid,
            "question": q,
            "expected": expected,
            "base_answer": base_answer,
            "rag_answer": rag_answer,
            "base_score": base_score,
            "rag_score": rag_score,
            "retrieved_docs": rag_output["top_docs"],
            "retrieval_scores": rag_output["scores"]
        })

        time.sleep(0.3)

    # Save JSON
    with open("rag_eval_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n🎉 Evaluation Completed!")
    print("📁 Saved → rag_eval_results.json")


if __name__ == "__main__":
    evaluate()
