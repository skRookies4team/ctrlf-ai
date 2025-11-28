from transformers import AutoTokenizer, AutoModelForCausalLM
import torch
import gc

MODELS = {
    "Llama3-8B-Instruct": "/home/team4/.cache/huggingface/hub/models--meta-llama--Meta-Llama-3-8B-Instruct/snapshots/8afb486c1db24fe5011ec46dfbe5b5dccdb575c2",
}

PROMPT = "아래 문장을 2줄로 요약해줘:\n\n사내 내부 문서 기반 LLM 성능 테스트 중입니다."

def test_model(model_name, model_path):
    print(f"\n===== {model_name} =====")

    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        dtype=torch.float16,
        device_map={"": "cuda:0"},
        trust_remote_code=True
    )

    inputs = tokenizer(PROMPT, return_tensors="pt").to(model.device)

    output = model.generate(
        **inputs,
        max_new_tokens=100,
        temperature=0.2
    )

    print(tokenizer.decode(output[0], skip_special_tokens=True))

    # 🔥 모델 메모리 완전 해제
    del model
    del tokenizer
    torch.cuda.empty_cache()
    gc.collect()


if __name__ == "__main__":
    for name, path in MODELS.items():
        test_model(name, path)
