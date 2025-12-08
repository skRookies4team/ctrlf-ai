# ctrlf-ai-gateway Dockerfile
# Python 3.12 기반 FastAPI 애플리케이션 컨테이너

# 베이스 이미지: Python 3.12 slim 버전 (경량화)
FROM python:3.12-slim

# 메타데이터 라벨
LABEL maintainer="CTRL+F Team"
LABEL description="CTRL+F AI Gateway Service"
LABEL version="0.1.0"

# 환경변수 설정
# Python 출력 버퍼링 비활성화 (로그 즉시 출력)
ENV PYTHONUNBUFFERED=1
# .pyc 파일 생성 방지
ENV PYTHONDONTWRITEBYTECODE=1
# pip 캐시 비활성화 (이미지 크기 감소)
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# 작업 디렉터리 설정
WORKDIR /app

# 시스템 의존성 설치 (필요시)
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     gcc \
#     && rm -rf /var/lib/apt/lists/*

# 비루트 유저 생성 (보안)
RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser

# requirements.txt 먼저 복사 (레이어 캐싱 최적화)
COPY requirements.txt .

# 의존성 설치
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사
COPY app/ ./app/

# 소유권 변경 (비루트 유저)
RUN chown -R appuser:appgroup /app

# 비루트 유저로 전환
USER appuser

# 포트 노출
EXPOSE 8000

# 헬스체크 설정
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=5).raise_for_status()" || exit 1

# 컨테이너 진입점
# uvicorn으로 FastAPI 앱 실행
# --host 0.0.0.0: 모든 인터페이스에서 수신
# --port 8000: 8000번 포트 사용
# 프로덕션에서는 --workers 옵션 추가 권장 (예: --workers 4)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
