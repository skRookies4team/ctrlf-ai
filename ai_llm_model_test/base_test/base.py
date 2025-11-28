from transformers import AutoTokenizer, AutoModelForCausalLM
import torch

MODELS = {
    "Qwen-7B": "/home/team4/qwen_7b",
    "Llama3-8B": "/home/team4/llama3_8b",
    "Gemma3-12B": "/home/team4/gemma3_12b",
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
