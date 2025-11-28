from transformers import AutoTokenizer, AutoModelForCausalLM
import torch

MODELS = {
    "Qwen2-7B-Instruct": "/home/team4/.cache/huggingface/hub/models--Qwen--Qwen2-7B-Instruct/snapshots/f2826a00ceef68f0f2b946d945ecc0477ce4450c",
    "Llama3-8B-Instruct": "/home/team4/.cache/huggingface/hub/models--meta-llama--Meta-Llama-3-8B-Instruct/snapshots/8afb486c1db24fe5011ec46dfbe5b5dccdb575c2",
    # "Gemma3-12B": "/home/team4/gemma3_12b"   # 만약 gemma는 직접 설치한 폴더
}

PROMPT = "아래 문장을 2줄로 요약해줘:\n\n사내 내부 문서 기반 LLM 성능 테스트 중입니다."

def test_model(model_name, model_path):
    print(f"\n===== {model_name} =====")
    
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        device_map="auto",
        torch_dtype=torch.float16,
        trust_remote_code=True
    )

    inputs = tokenizer(PROMPT, return_tensors="pt").to("cuda")

    output = model.generate(
        **inputs,
        max_new_tokens=150,
        temperature=0.2
    )

    print(tokenizer.decode(output[0], skip_special_tokens=True))


if __name__ == "__main__":
    for name, path in MODELS.items():
        test_model(name, path)
