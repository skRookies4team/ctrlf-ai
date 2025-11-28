import json
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from difflib import SequenceMatcher
import gc


EVAL_FILE = "emp_eval_50.json"

MODELS = {
    "Qwen2-7B-Instruct": "/home/team4/.cache/huggingface/hub/models--Qwen--Qwen2-7B-Instruct/snapshots/f2826a00ceef68f0f2b946d945ecc0477ce4450c",
    "Llama3-8B-Instruct": "/home/team4/.cache/huggingface/hub/models--meta-llama--Meta-Llama-3-8B-Instruct/snapshots/8afb486c1db24fe5011ec46dfbe5b5dccdb575c2"
}


def load_data():
    with open(EVAL_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def load_model(model_path, use_8bit=False):
    if use_8bit:
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            load_in_8bit=True,
            device_map="auto",
            trust_remote_code=True
        )
    else:
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            dtype=torch.float16,
            device_map={"": "cuda:0"},
            trust_remote_code=True
        )
    return model


def evaluate_model(model_name, model_path, eval_data, use_8bit=False):
    print(f"\n\n===== Running Evaluation: {model_name} =====")

    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token

    model = load_model(model_path, use_8bit=use_8bit)
    model.eval()

    scores = []
    results = []

    for item in eval_data:
        question = item["question"]
        expected = item["expected_answer"]

        inputs = tokenizer(question, return_tensors="pt").to("cuda")
        output = model.generate(
            **inputs,
            max_new_tokens=150,
            temperature=0.1,
        )

        answer = tokenizer.decode(output[0], skip_special_tokens=True)
        score = similarity(answer, expected)

        scores.append(score)
        results.append({
            "question": question,
            "expected": expected,
            "answer": answer,
            "score": score
        })

        print(f"\nQ: {question}")
        print(f"✓ Model: {answer}")
        print(f"✓ Expected: {expected}")
        print(f"→ Score: {score:.3f}")

    avg_score = sum(scores) / len(scores)
    print(f"\n==== {model_name} FINAL SCORE: {avg_score:.3f} ====")

    # 메모리 해제
    del model
    del tokenizer
    torch.cuda.empty_cache()
    gc.collect()

    return avg_score, results


if __name__ == "__main__":
    eval_data = load_data()

    # Qwen2는 FP16로 충분히 들어감
    qwen_score, qwen_results = evaluate_model(
        "Qwen2-7B-Instruct",
        MODELS["Qwen2-7B-Instruct"],
        eval_data,
        use_8bit=False
    )

    # Llama3는 메모리 부족 → 강제로 8bit 사용
    llama_score, llama_results = evaluate_model(
        "Llama3-8B-Instruct",
        MODELS["Llama3-8B-Instruct"],
        eval_data,
        use_8bit=True
    )

    print("\n\n================== FINAL COMPARISON ==================")
    print(f"Qwen2-7B Score   : {qwen_score:.3f}")
    print(f"Llama3-8B Score  : {llama_score:.3f}")

    if qwen_score > llama_score:
        print("🏆 Winner: Qwen2-7B-Instruct")
    elif llama_score > qwen_score:
        print("🏆 Winner: Llama3-8B-Instruct")
    else:
        print("⚖️  Draw")
