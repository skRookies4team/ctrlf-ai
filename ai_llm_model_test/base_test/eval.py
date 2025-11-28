import json
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from openai import OpenAI
from dotenv import load_dotenv
import os
import gc
import time

# === 📌 .env 파일 로드 ===
load_dotenv()

EVAL_FILE = "emp_eval_50.json"

MODELS = {
    "Qwen2-7B-Instruct": "/home/team4/.cache/huggingface/hub/models--Qwen--Qwen2-7B-Instruct/snapshots/f2826a00ceef68f0f2b946d945ecc0477ce4450c",
    "Llama3-8B-Instruct": "/home/team4/.cache/huggingface/hub/models--meta-llama--Meta-Llama-3-8B-Instruct/snapshots/8afb486c1db24fe5011ec46dfbe5b5dccdb575c2"
}

# === 📌 GPT-4o 클라이언트 생성 ===
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
{"score": float}
"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    return json.loads(response.choices[0].message.content)["score"]

# (evaluate_model 생략: 너가 기존 코드 그대로 유지하면 됨)
