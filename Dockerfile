# CTRL-F AI 문서 검색 시스템 - Docker Image
# Python 3.12 + hwp5txt + LibreOffice

FROM python:3.12-slim

# 메타데이터
LABEL maintainer="skRookies4team"
LABEL description="CTRL-F AI RAG System with HWP support"

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 업데이트 및 필수 도구 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    # HWP 변환 도구 (세희 방식)
    hwp5 \
    # LibreOffice (대체 HWP 변환 방법)
    libreoffice \
    libreoffice-writer \
    # OCR 도구
    tesseract-ocr \
    tesseract-ocr-kor \
    # PDF 처리 도구
    poppler-utils \
    # 빌드 도구
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 파일 복사
COPY requirements.txt .

# Python 패키지 설치 (필수 의존성)
RUN pip install --no-cache-dir -r requirements.txt

# hwp5 Python 패키지 설치 (세희 방식)
RUN pip install --no-cache-dir hwp5

# 선택적 의존성 (Qwen3 임베딩)
RUN pip install --no-cache-dir \
    langchain-huggingface \
    langchain-community \
    sentence-transformers \
    torch --index-url https://download.pytorch.org/whl/cpu

# 애플리케이션 코드 복사
COPY . .

# 데이터 디렉토리 생성
RUN mkdir -p data/vector_store uploads

# 환경변수 설정
ENV PYTHONUNBUFFERED=1 \
    EMBEDDING_PROVIDER=qwen3 \
    EMBEDDING_DIM=384 \
    API_BASE_URL=http://localhost:8000

# 포트 노출
EXPOSE 8000 8501

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')"

# 실행 명령 (FastAPI 서버)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
