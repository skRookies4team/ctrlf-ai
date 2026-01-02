# Mock RAGFlow Server Dockerfile
# Phase 8: Integration Testing

FROM python:3.12-slim

LABEL maintainer="CTRL+F Team"
LABEL description="Mock RAGFlow Server for Integration Testing"

# 환경변수 설정
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# curl 설치 (헬스체크용)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 의존성 설치
RUN pip install --no-cache-dir fastapi uvicorn pydantic python-multipart

# 애플리케이션 복사
COPY main.py .

# 포트 노출
EXPOSE 8080

# 실행
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
