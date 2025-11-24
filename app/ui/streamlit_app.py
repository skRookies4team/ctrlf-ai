# streamlit_app.py
import sys
import os
import streamlit as st
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from typing import Optional, Dict, Any
from pathlib import Path
import tempfile

# ===============================
# core 모듈 import 경로 설정
# ===============================
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
from core.cleaner import clean_text
from core.chunker import chunk_text, chunk_by_paragraphs, chunk_by_headings
from core.hwp_converter import convert_hwp_to_text
from core.vector_store import get_vector_store
from core.embedder import embed_texts

# ===============================
# 한글 폰트 설정 (Windows)
# ===============================
try:
    font_path = "C:/Windows/Fonts/malgun.ttf"
    if os.path.exists(font_path):
        font_prop = fm.FontProperties(fname=font_path)
        plt.rcParams['font.family'] = font_prop.get_name()
        plt.rcParams['axes.unicode_minus'] = False
    else:
        plt.rcParams['font.family'] = 'Malgun Gothic'
except Exception as e:
    print("폰트 설정 오류:", e)
    plt.rcParams['font.family'] = 'sans-serif'

# ===============================
# API 기본 URL
# ===============================
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

# ===============================
# Streamlit 페이지 설정
# ===============================
st.set_page_config(
    page_title="문서 검색 & HWP 전처리 시스템",
    page_icon="📚",
    layout="wide"
)

# ===============================
# 헬스체크
# ===============================
def check_system_health() -> Dict[str, Any]:
    try:
        response = requests.get(f"{API_BASE_URL}/api/v1/rag/health", timeout=30)
        if response.ok:
            return response.json()
        return {"status": "unhealthy", "message": "API 연결 실패"}
    except Exception as e:
        return {"status": "error", "message": f"{str(e)}"}

# ===============================
# 벡터 초기화
# ===============================
def reset_vector_store():
    try:
        response = requests.post(f"{API_BASE_URL}/api/v1/rag/reset", timeout=60)
        if response.ok:
            return response.json()
        else:
            st.error(f"벡터 초기화 실패: {response.text}")
            return None
    except Exception as e:
        st.error(f"벡터 초기화 오류: {str(e)}")
        return None

# ===============================
# API 업로드/검색/RAG
# ===============================
def upload_file(file, chunk_strategy: str, max_chars: int, overlap_chars: int, use_ocr: bool):
    try:
        files = {"file": (file.name, file, file.type)}
        data = {
            "chunk_strategy": chunk_strategy,
            "max_chars": max_chars,
            "overlap_chars": overlap_chars,
            "use_ocr_fallback": use_ocr
        }
        response = requests.post(
            f"{API_BASE_URL}/api/v1/ingest/file",
            files=files,
            data=data,
            timeout=600
        )
        if response.ok:
            return response.json()
        else:
            st.error(f"업로드 실패: {response.text}")
            return None
    except Exception as e:
        st.error(f"업로드 오류: {str(e)}")
        return None

def search_documents(query: str, top_k: int):
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/rag/query",
            json={"query": query, "top_k": top_k, "include_context": True},
            timeout=60
        )
        if response.ok:
            return response.json()
        else:
            st.error(f"검색 실패: {response.text}")
            return None
    except Exception as e:
        st.error(f"검색 오류: {str(e)}")
        return None

def generate_answer(query: str, top_k: int, max_tokens: int, llm_type: Optional[str] = None):
    try:
        payload = {"query": query, "top_k": top_k, "max_tokens": max_tokens}
        if llm_type:
            payload["llm_type"] = llm_type

        response = requests.post(
            f"{API_BASE_URL}/api/v1/rag/answer",
            json=payload,
            timeout=120
        )
        if response.ok:
            return response.json()
        else:
            st.error(f"답변 생성 실패: {response.text}")
            return None
    except Exception as e:
        st.error(f"답변 생성 오류: {str(e)}")
        return None

# ===============================
# 벡터 스토어에 로컬 청크 삽입 (FAISS 호환)
# ===============================
def insert_chunks_to_vector_store(ingest_id: str, chunks_list):
    try:
        vs = get_vector_store(dim=384)
        vectors = embed_texts(chunks_list)
        # 단일 벡터 반복 대신 한 번에 add_vectors 호출
        metadatas = [{"ingest_id": ingest_id, "chunk_index": i, "text": chunks_list[i]} for i in range(len(chunks_list))]
        vs.add_vectors(vectors, metadatas)
        return len(chunks_list)
    except Exception as e:
        st.error(f"벡터 삽입 오류: {str(e)}")
        return 0

# ===============================
# 로컬 전처리/청킹 (HWP 지원)
# ===============================
def process_file_local(file, chunk_strategy: str, max_chars: int, overlap_chars: int):
    try:
        file_ext = os.path.splitext(file.name)[1].lower()
        raw_text = ""

        if file_ext == ".pdf":
            from PyPDF2 import PdfReader
            reader = PdfReader(file)
            raw_text = "\n".join([p.extract_text() or "" for p in reader.pages])
        elif file_ext in [".txt", ".md"]:
            raw_text = file.read().decode("utf-8", errors="ignore")
        elif file_ext == ".hwp":
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".hwp") as tmp:
                    tmp.write(file.read())
                    tmp_path = tmp.name
                raw_text = convert_hwp_to_text(tmp_path)
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
        else:
            st.warning(f"{file_ext} 형식은 지원되지 않습니다. PDF/TXT/HWP만 가능.")
            return None

        cleaned_text = clean_text(raw_text)

        if chunk_strategy == "character_window":
            chunks_list = chunk_text(cleaned_text, max_chars=max_chars, overlap_chars=overlap_chars)
        elif chunk_strategy == "paragraph_based":
            chunks_list = chunk_by_paragraphs([{"section": "전체", "content": cleaned_text}], max_chars=max_chars)
        elif chunk_strategy == "heading_based":
            chunks_list = chunk_by_headings([{"section": "전체", "content": cleaned_text}], max_chars=max_chars)
        else:
            chunks_list = [cleaned_text]

        chunk_lengths = [len(c) for c in chunks_list]

        ingest_id = "local_" + file.name
        # 벡터 스토어 삽입
        inserted_count = insert_chunks_to_vector_store(ingest_id, chunks_list)

        return {
            "ingest_id": ingest_id,
            "status": "OK",
            "num_chunks": len(chunks_list),
            "raw_text_len": len(raw_text),
            "cleaned_text_len": len(cleaned_text),
            "chunk_texts": chunks_list,
            "chunk_lengths": chunk_lengths,
            "inserted_chunks": inserted_count,
            "cleaned_text": cleaned_text
        }

    except Exception as e:
        st.error(f"로컬 처리 오류: {str(e)}")
        return None

# ===============================
# Streamlit UI
# ===============================
def main():
    st.title("📚 문서 검색 & HWP 전처리 시스템")

    # -------------------------
    # 사이드바
    # -------------------------
    with st.sidebar:
        st.header("⚙️ 시스템 상태")
        if st.button("상태 새로고침"):
            st.experimental_rerun()

        health = check_system_health()
        status_color = {"healthy":"🟢","degraded":"🟡","unhealthy":"🔴","error":"⚫"}
        st.write(f"{status_color.get(health.get('status','error'),'⚫')} **{health.get('status','unknown').upper()}**")
        st.write(f"메시지: {health.get('message','N/A')}")
        if health.get("status") in ["healthy","degraded"]:
            st.metric("벡터 개수", health.get("total_vectors",0))
            st.write(f"임베더: {'✅' if health.get('embedder_available') else '❌'}")
            st.write(f"벡터스토어: {'✅' if health.get('vector_store_available') else '❌'}")
            st.write(f"LLM: {'✅' if health.get('llm_available') else '❌'} ({health.get('llm_type','N/A')})")
        st.divider()
        st.caption(f"API: {API_BASE_URL}")

    # -------------------------
    # 탭1: 파일 업로드
    # -------------------------
    tab1, tab2, tab3 = st.tabs(["📤 파일 업로드", "🔍 문서 검색", "💬 질문하기"])
    with tab1:
        st.header("파일 업로드 및 처리")
        uploaded_file = st.file_uploader("문서 파일 선택", type=["pdf","txt","hwp"])

        col1, col2 = st.columns(2)
        with col1:
            chunk_strategy = st.selectbox("청킹 전략", ["character_window","paragraph_based","heading_based"])
            max_chars = st.slider("최대 청크 크기", 500, 3000, 1000, 100)
        with col2:
            overlap_chars = st.slider("청크 겹침", 0, 500, 200, 50)
            use_local = st.checkbox("로컬 전처리 사용", value=True)

        if uploaded_file and st.button("파일 처리 시작"):
            with st.spinner("파일 처리 중..."):
                if use_local:
                    result = process_file_local(uploaded_file, chunk_strategy, max_chars, overlap_chars)
                else:
                    result = upload_file(uploaded_file, chunk_strategy, max_chars, overlap_chars, use_ocr=True)

            if result:
                st.success(f"✅ 처리 완료! (Ingest ID: {result['ingest_id']})")
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("상태", result.get("status","N/A"))
                col2.metric("청크 개수", result.get("num_chunks",0))
                col3.metric("원본 텍스트", f"{result.get('raw_text_len',0):,} 자")
                col4.metric("정제 후", f"{result.get('cleaned_text_len',0):,} 자")
                col5.metric("벡터 삽입 완료", result.get("inserted_chunks",0))

                st.subheader("📝 전처리된 텍스트")
                preview_text = result.get("cleaned_text","")[:20000]
                st.text_area("전처리 결과 (최대 2만자)", value=preview_text, height=300)

                if "chunk_texts" in result:
                    st.subheader("📚 청크별 텍스트")
                    for i, chunk_text in enumerate(result["chunk_texts"]):
                        with st.expander(f"청크 {i+1} (길이: {len(chunk_text)})"):
                            st.text_area(f"청크 {i+1}", value=chunk_text, height=200)

    # -------------------------
    # 탭2: 문서 검색
    # -------------------------
    with tab2:
        st.header("문서 검색")
        search_query = st.text_input("검색 질문")
        search_top_k = st.slider("검색 결과 개수",1,20,5)
        if search_query and st.button("검색"):
            with st.spinner("검색 중..."):
                results = search_documents(search_query, search_top_k)
            if results:
                st.success(f"✅ {results.get('total_retrieved',0)}개 결과 발견")
                for i, chunk in enumerate(results.get("retrieved_chunks",[])):
                    with st.expander(f"결과 {i+1} - {chunk.get('file_name','N/A')}"):
                        st.write(chunk.get("text",""))

    # -------------------------
    # 탭3: 질문하기
    # -------------------------
    with tab3:
        st.header("질문하기 (RAG)")
        rag_query = st.text_area("질문 입력")
        col1, col2 = st.columns(2)
        with col1:
            rag_top_k = st.slider("참조 문서 개수", 1, 10, 5)
        with col2:
            rag_max_tokens = st.slider("최대 생성 토큰", 100, 2000, 500, 100)
        rag_llm_type = st.selectbox("LLM 타입", ["auto","mock","openai"])
        if rag_query and st.button("답변 생성"):
            with st.spinner("답변 생성 중..."):
                llm_type_param = None if rag_llm_type=="auto" else rag_llm_type
                answer_result = generate_answer(rag_query, rag_top_k, rag_max_tokens, llm_type_param)
            if answer_result:
                st.success("✅ 답변 생성 완료")
                st.info(f"🤖 사용된 LLM: {answer_result.get('llm_type','N/A')}")
                st.subheader("💡 답변")
                st.markdown(answer_result.get("answer","답변 없음"))
                st.subheader("📄 참조 문서")
                chunks = answer_result.get("retrieved_chunks", [])
                if chunks:
                    df_data = [
                        {"순위":i+1,"파일명":c.get("file_name","N/A"),"청크":c.get("chunk_index",0),
                         "유사도":f"{c.get('score',0):.4f}"} for i,c in enumerate(chunks)
                    ]
                    st.dataframe(pd.DataFrame(df_data), use_container_width=True)
                    for i, chunk in enumerate(chunks):
                        with st.expander(f"상세 내용 {i+1}"):
                            st.write(chunk.get("text",""))

if __name__ == "__main__":
    main()
