import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from rag.retriever import retrieve_context


def load_local_model(model_path: str, use_8bit: bool = True):
    """
    로컬 LLM을 로딩 (메모리 절약을 위해 8bit 기본값)
    """
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
            torch_dtype=torch.float16,
            device_map="auto",
            trust_remote_code=True
        )

    tokenizer = AutoTokenizer.from_pretrained(
        model_path,
        trust_remote_code=True
    )
    tokenizer.pad_token = tokenizer.eos_token

    return model, tokenizer


def build_rag_prompt(question: str, context: str) -> str:
    """
    RAG용 시스템 + 컨텍스트 + 질문을 하나의 Prompt로 묶기
    """
    return f"""
당신은 회사 내부 규정/보안/인사/근태/교육 등을 정확하게 설명하는 HR·보안 전문 챗봇입니다.

아래는 검색된 사내 문서 컨텍스트입니다.
이 내용을 기반으로 반드시 사실적으로 답변하세요.
추측하거나 문서를 벗어난 정보를 만들지 마세요.

### 📚 문서 컨텍스트
{context}

---

### ❓ 질문
{question}

### ✨ 답변 (한국어로 명확하고 간단하게):
"""


def generate_rag_answer(
    question: str,
    model_path: str,
    use_8bit: bool = True,
    top_k: int = 5
):
    """
    RAG 전체 실행: Retrieve → Build Prompt → Generate
    """

    # 1) 검색문서 가져오기
    retriever_result = retrieve_context(question, top_k)
    context = retriever_result["context"]

    # 2) Prompt 구성
    prompt = build_rag_prompt(question, context)

    # 3) 로컬 모델 로딩
    model, tokenizer = load_local_model(model_path, use_8bit)

    # 4) 모델 추론
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    output = model.generate(
        **inputs,
        max_new_tokens=300,
        temperature=0.2,
        top_p=0.9,
    )

    answer = tokenizer.decode(output[0], skip_special_tokens=True)

    return {
        "question": question,
        "context": context,
        "answer": answer,
        "top_docs": retriever_result["documents"],
        "scores": retriever_result["scores"]
    }


# === 간단 실행 테스트 ===
if __name__ == "__main__":
    MODEL_PATH = "/home/team4/.cache/huggingface/hub/models--Qwen--Qwen2-7B-Instruct/snapshots/f2826a00ceef68f0f2b946d945ecc0477ce4450c"

    query = "재택근무 신청 절차가 어떻게 돼?"
    result = generate_rag_answer(query, MODEL_PATH)

    print("\n📌 [RAG ANSWER]")
    print(result["answer"])

    print("\n--- Retrieved Docs ---")
    for i, doc in enumerate(result["top_docs"], 1):
        print(f"\n[{i}]")
        print(doc)
