import json
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from openai import OpenAI
from dotenv import load_dotenv
import os
import gc
import time

# === .env 파일 로드 ===
load_dotenv()

EVAL_FILE = "emp_eval_50.json"

MODELS = {
    "Qwen2-7B-Instruct": "/home/team4/.cache/huggingface/hub/models--Qwen--Qwen2-7B-Instruct/snapshots/f2826a00ceef68f0f2b946d945ecc0477ce4450c",
    "Llama3-8B-Instruct": "/home/team4/.cache/huggingface/hub/models--meta-llama--Meta-Llama-3-8B-Instruct/snapshots/8afb486c1db24fe5011ec46dfbe5b5dccdb575c2"
}

# === GPT-4o 클라이언트 ===
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def load_data():
    with open(EVAL_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def load_model(model_path, use_8bit=False):
    if use_8bit:
        return AutoModelForCausalLM.from_pretrained(
            model_path,
            load_in_8bit=True,
            device_map="auto",
            trust_remote_code=True
        )
    return AutoModelForCausalLM.from_pretrained(
        model_path,
        dtype=torch.float16,
        device_map={"": "cuda:0"},
        trust_remote_code=True
    )

def llm_judge_score(question, expected, answer):
    prompt = f"""
다음은 LLM 성능 평가입니다.  
너의 역할은 공정한 평가자(Judge)이며, 모델 답변이 정답과 의미적으로 얼마나 일치하는지 판단해야 합니다.

### 질문
{question}

### 정답(정답자)
{expected}

### 모델의 답변
{answer}

0~1 사이 점수로 평가하되 규칙은 다음과 같다.
- 의미가 매우 정확히 같으면: 0.9~1.0
- 핵심 내용 대부분 같으면: 0.7~0.89
- 절반 정도 맞으면: 0.4~0.69
- 일부만 맞으면: 0.1~0.39
- 전혀 맞지 않으면: 0.0~0.09

출력은 JSON 형식으로 한 줄만:
{{"score": float}}
"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    return json.loads(response.choices[0].message.content)["score"]


# =====================================================
# 🔥 여기서부터 실제 평가 함수 (로그 출력 포함)
# =====================================================
def evaluate_model(model_name, model_path, eval_data, use_8bit=False):
    print(f"\n===== Evaluating {model_name} =====")

    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token
    model = load_model(model_path, use_8bit=use_8bit)
    model.eval()

    scores = []

    for item in eval_data:
        question = item["question"]
        expected = item["expected_answer"]

        # 모델 답변 생성
        inputs = tokenizer(question, return_tensors="pt").to(model.device)
        output = model.generate(
            **inputs,
            max_new_tokens=150,
            temperature=0.2
        )
        answer = tokenizer.decode(output[0], skip_special_tokens=True)

        # GPT-4o로 평가
        score = llm_judge_score(question, expected, answer)

        print(f"\nQ: {question}")
        print(f"Model Answer : {answer}")
        print(f"Expected     : {expected}")
        print(f"Judge Score  : {score:.3f}")

        scores.append(score)

        time.sleep(0.3)

    avg_score = sum(scores) / len(scores)
    print(f"\n🔥 {model_name} FINAL SCORE: {avg_score:.3f}")

    del model
    del tokenizer
    torch.cuda.empty_cache()
    gc.collect()

    return avg_score


# =====================================================
# 🔥 main 실행부
# =====================================================
if __name__ == "__main__":
    eval_data = load_data()

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

    if qwen_score > llama_score:
        print("🏆 Winner: Qwen2-7B-Instruct")
    else:
        print("🏆 Winner: Llama3-8B-Instruct")
