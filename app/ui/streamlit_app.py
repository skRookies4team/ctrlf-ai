"""
Streamlit UI - 문서 검색 및 RAG 시스템

실행 방법:
    streamlit run app/ui/streamlit_app.py

기능:
1. 파일 업로드 및 처리
2. 문서 검색
3. RAG 질문-답변
4. 처리 통계 시각화
"""

import streamlit as st
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from typing import Optional, Dict, Any
import os

# 한글 폰트 설정 (Windows)
plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False

# API 기본 URL
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

# 페이지 설정
st.set_page_config(
    page_title="문서 검색 시스템",
    page_icon="📚",
    layout="wide"
)


def check_system_health() -> Dict[str, Any]:
    """시스템 헬스체크"""
    try:
        response = requests.get(f"{API_BASE_URL}/api/v1/rag/health", timeout=5)
        if response.ok:
            return response.json()
        return {"status": "unhealthy", "message": "API 연결 실패"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def upload_file(file, chunk_strategy: str, max_chars: int, overlap_chars: int, use_ocr: bool):
    """파일 업로드 및 처리"""
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
            timeout=300
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
    """문서 검색"""
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/rag/query",
            json={"query": query, "top_k": top_k, "include_context": True},
            timeout=30
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
    """RAG 답변 생성"""
    try:
        payload = {
            "query": query,
            "top_k": top_k,
            "max_tokens": max_tokens
        }
        if llm_type:
            payload["llm_type"] = llm_type

        response = requests.post(
            f"{API_BASE_URL}/api/v1/rag/answer",
            json=payload,
            timeout=60
        )
        if response.ok:
            return response.json()
        else:
            st.error(f"답변 생성 실패: {response.text}")
            return None
    except Exception as e:
        st.error(f"답변 생성 오류: {str(e)}")
        return None


def main():
    st.title("📚 문서 검색 및 RAG 시스템")

    # 사이드바: 시스템 상태
    with st.sidebar:
        st.header("⚙️ 시스템 상태")

        if st.button("상태 새로고침"):
            st.rerun()

        health = check_system_health()

        status_color = {
            "healthy": "🟢",
            "degraded": "🟡",
            "unhealthy": "🔴",
            "error": "⚫"
        }

        st.write(f"{status_color.get(health.get('status', 'error'), '⚫')} **{health.get('status', 'unknown').upper()}**")
        st.write(f"메시지: {health.get('message', 'N/A')}")

        if health.get("status") == "healthy" or health.get("status") == "degraded":
            st.metric("벡터 개수", health.get("total_vectors", 0))
            st.write(f"임베더: {'✅' if health.get('embedder_available') else '❌'}")
            st.write(f"벡터스토어: {'✅' if health.get('vector_store_available') else '❌'}")
            st.write(f"LLM: {'✅' if health.get('llm_available') else '❌'} ({health.get('llm_type', 'N/A')})")

        st.divider()
        st.caption(f"API: {API_BASE_URL}")

    # 메인 탭
    tab1, tab2, tab3 = st.tabs(["📤 파일 업로드", "🔍 문서 검색", "💬 질문하기"])

    # 탭1: 파일 업로드
    with tab1:
        st.header("파일 업로드 및 처리")

        uploaded_file = st.file_uploader(
            "문서 파일 선택",
            type=["pdf", "hwp", "docx", "pptx"],
            help="PDF, HWP, DOCX, PPTX 파일을 업로드할 수 있습니다"
        )

        col1, col2 = st.columns(2)

        with col1:
            chunk_strategy = st.selectbox(
                "청킹 전략",
                ["character_window", "paragraph_based", "heading_based"],
                help="character_window: 고정 크기 윈도우, paragraph_based: 문단 기반, heading_based: 제목 기반"
            )
            max_chars = st.slider("최대 청크 크기", 500, 3000, 1000, 100)

        with col2:
            overlap_chars = st.slider("청크 겹침", 0, 500, 200, 50)
            use_ocr = st.checkbox("OCR 폴백 사용", value=True, help="텍스트 추출 실패 시 OCR 사용")

        if uploaded_file is not None:
            if st.button("파일 처리 시작", type="primary"):
                with st.spinner("파일 처리 중..."):
                    result = upload_file(uploaded_file, chunk_strategy, max_chars, overlap_chars, use_ocr)

                if result:
                    st.success(f"✅ 처리 완료! (Ingest ID: {result['ingest_id']})")

                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("상태", result.get("status", "N/A"))
                    col2.metric("청크 개수", result.get("num_chunks", 0))
                    col3.metric("원본 텍스트", f"{result.get('raw_text_len', 0):,} 자")
                    col4.metric("정제 후", f"{result.get('cleaned_text_len', 0):,} 자")

                    # 모니터링 데이터 시각화
                    if "monitoring" in result:
                        st.subheader("📊 처리 통계")
                        monitoring = result["monitoring"]

                        # 청크 길이 분포
                        if "chunking" in monitoring and "chunk_lengths" in monitoring["chunking"]:
                            chunk_lengths = monitoring["chunking"]["chunk_lengths"]

                            fig, ax = plt.subplots(figsize=(10, 4))
                            ax.hist(chunk_lengths, bins=20, edgecolor='black', alpha=0.7)
                            ax.set_xlabel("청크 길이 (자)")
                            ax.set_ylabel("빈도")
                            ax.set_title("청크 길이 분포")
                            ax.axvline(max_chars, color='red', linestyle='--', label=f'max_chars={max_chars}')
                            ax.legend()
                            st.pyplot(fig)

                            # 통계 정보
                            col1, col2, col3, col4 = st.columns(4)
                            col1.metric("평균", f"{monitoring['chunking'].get('chunk_len_avg', 0):.1f}")
                            col2.metric("최소", monitoring['chunking'].get('chunk_len_min', 0))
                            col3.metric("최대", monitoring['chunking'].get('chunk_len_max', 0))
                            col4.metric("표준편차", f"{monitoring['chunking'].get('chunk_len_std', 0):.1f}")

    # 탭2: 문서 검색
    with tab2:
        st.header("문서 검색")

        search_query = st.text_input("검색 질문", placeholder="예: 구매 절차는 어떻게 되나요?")
        search_top_k = st.slider("검색 결과 개수", 1, 20, 5)

        if st.button("검색", type="primary") and search_query:
            with st.spinner("검색 중..."):
                results = search_documents(search_query, search_top_k)

            if results:
                st.success(f"✅ {results.get('total_retrieved', 0)}개 결과 발견")

                for i, chunk in enumerate(results.get("retrieved_chunks", [])):
                    with st.expander(f"**결과 {i+1}** - {chunk.get('file_name', 'N/A')} (유사도: {chunk.get('score', 0):.3f})"):
                        st.write(f"**출처:** {chunk.get('file_name', 'N/A')}")
                        st.write(f"**청크 인덱스:** {chunk.get('chunk_index', 0)}")
                        st.write(f"**청킹 전략:** {chunk.get('strategy', 'N/A')}")
                        st.write(f"**유사도 점수:** {chunk.get('score', 0):.4f} (낮을수록 유사)")
                        st.divider()
                        st.write(chunk.get("text", "텍스트 없음"))

    # 탭3: 질문하기 (RAG)
    with tab3:
        st.header("질문하기 (RAG)")

        rag_query = st.text_area(
            "질문을 입력하세요",
            placeholder="예: 구매업무처리규정에 따르면 구매 요청은 어떻게 해야 하나요?",
            height=100
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            rag_top_k = st.slider("참조 문서 개수", 1, 10, 5, key="rag_top_k")
        with col2:
            rag_max_tokens = st.slider("최대 생성 토큰", 100, 2000, 500, 100, key="rag_max_tokens")
        with col3:
            rag_llm_type = st.selectbox(
                "LLM 타입",
                ["auto", "mock", "openai"],
                help="auto: 자동 선택, mock: 개발용 더미, openai: OpenAI GPT (ENABLE_OPENAI=true 필요)"
            )

        if st.button("답변 생성", type="primary") and rag_query:
            with st.spinner("답변 생성 중..."):
                llm_type_param = None if rag_llm_type == "auto" else rag_llm_type
                answer_result = generate_answer(rag_query, rag_top_k, rag_max_tokens, llm_type_param)

            if answer_result:
                st.success("✅ 답변 생성 완료")

                # LLM 정보
                st.info(f"🤖 사용된 LLM: **{answer_result.get('llm_type', 'N/A')}**")

                # 답변 표시
                st.subheader("💡 답변")
                st.markdown(answer_result.get("answer", "답변 없음"))

                st.divider()

                # 참조 문서
                st.subheader("📄 참조 문서")
                chunks = answer_result.get("retrieved_chunks", [])

                if chunks:
                    # 유사도 테이블
                    df_data = []
                    for i, chunk in enumerate(chunks):
                        df_data.append({
                            "순위": i + 1,
                            "파일명": chunk.get("file_name", "N/A"),
                            "청크": chunk.get("chunk_index", 0),
                            "유사도": f"{chunk.get('score', 0):.4f}"
                        })

                    df = pd.DataFrame(df_data)
                    st.dataframe(df, use_container_width=True)

                    # 각 청크 상세
                    for i, chunk in enumerate(chunks):
                        with st.expander(f"상세 내용 {i+1}"):
                            st.write(chunk.get("text", "텍스트 없음"))


if __name__ == "__main__":
    main()
