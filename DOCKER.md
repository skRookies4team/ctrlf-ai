# Docker 실행 가이드

## 개요

Docker를 사용하면 HWP 변환 도구(`hwp5txt`)가 자동으로 설치된 Linux 환경에서 CTRL-F AI 시스템을 실행할 수 있습니다.

## 사전 요구사항

- Docker Desktop 설치 (Windows/Mac)
- 또는 Docker Engine (Linux)

## 빠른 시작

### 1. Docker 이미지 빌드

```bash
# 프로젝트 루트에서 실행
docker build -t ctrlf-ai .
```

빌드 시간: 약 5-10분 (hwp5, LibreOffice, Qwen3 설치 포함)

### 2. 컨테이너 실행

#### 방법 1: Docker Compose (권장)

```bash
# FastAPI + Streamlit UI 동시 실행
docker-compose up

# 백그라운드 실행
docker-compose up -d

# 로그 확인
docker-compose logs -f

# 중지
docker-compose down
```

**접속**:
- FastAPI: http://localhost:8000/docs
- Streamlit UI: http://localhost:8501

#### 방법 2: Docker Run (FastAPI만)

```bash
docker run -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/uploads:/app/uploads \
  -e EMBEDDING_PROVIDER=qwen3 \
  -e ENABLE_OPENAI=false \
  ctrlf-ai
```

**접속**: http://localhost:8000/docs

## 환경변수 설정

### .env 파일 생성

```bash
# .env.example 복사
cp .env.example .env

# 편집
nano .env
```

### 주요 환경변수

```bash
# 임베딩 설정
EMBEDDING_PROVIDER=qwen3  # dummy, qwen3, openai
EMBEDDING_DIM=384

# OpenAI 설정 (선택적)
ENABLE_OPENAI=true
OPENAI_API_KEY=sk-proj-your-api-key-here
OPENAI_MODEL=gpt-3.5-turbo

# API URL (Streamlit UI용)
API_BASE_URL=http://localhost:8000
```

## HWP 파일 테스트

### 1. 컨테이너 실행 확인

```bash
# HWP 변환 도구 확인
docker exec -it ctrlf-api hwp5txt --version

# 또는 docker-compose 사용 시
docker-compose exec api hwp5txt --version
```

**예상 출력**:
```
hwp5 0.x.x
```

### 2. HWP 파일 업로드 테스트

#### Streamlit UI 사용

1. http://localhost:8501 접속
2. "문서 업로드" 탭
3. HWP 파일 선택
4. 청킹 전략: `heading_based`
5. "처리 시작" 클릭

#### cURL 사용

```bash
# HWP 파일 업로드
curl -X POST "http://localhost:8000/api/v1/ingest/file" \
  -F "file=@구매업무처리규정.hwp" \
  -F "chunk_strategy=heading_based" \
  -F "max_chars=2000"
```

**성공 응답**:
```json
{
  "ingest_id": "a1b2c3d4...",
  "file_name": "구매업무처리규정.hwp",
  "status": "OK",
  "num_chunks": 15,
  "chunk_strategy": "heading_based"
}
```

### 3. 로그 확인

```bash
# FastAPI 로그
docker-compose logs -f api | grep HWP

# 예상 로그:
# [HWP] Extracting text from: 구매업무처리규정.hwp
# [hwp5txt] Converting HWP: 구매업무처리규정.hwp
# [hwp5txt] Extracted 15234 chars from 구매업무처리규정.hwp
# Successfully extracted 15234 characters from HWP
```

## 데이터 영속화

Docker 볼륨 마운트로 데이터 보존:

```yaml
# docker-compose.yml에 이미 설정됨
volumes:
  - ./data:/app/data          # FAISS 인덱스
  - ./uploads:/app/uploads    # 업로드 파일
```

**재시작 후에도 데이터 유지됨**

## 문제 해결

### 1. hwp5txt not found

**증상**: `hwp5txt: command not found`

**해결**:
```bash
# 이미지 재빌드
docker-compose build --no-cache
```

### 2. HWP 변환 실패

**증상**: "All HWP conversion methods failed"

**해결**:
```bash
# 컨테이너 진입
docker exec -it ctrlf-api bash

# hwp5 수동 테스트
hwp5txt /path/to/file.hwp

# 결과 확인
```

### 3. 메모리 부족

**증상**: "Killed" 또는 "Out of memory"

**해결**:
```bash
# Docker Desktop 설정에서 메모리 증가
# Settings > Resources > Memory: 4GB 이상
```

### 4. 포트 충돌

**증상**: "Address already in use"

**해결**:
```bash
# docker-compose.yml 수정
ports:
  - "8001:8000"  # 다른 포트 사용
  - "8502:8501"
```

## 프로덕션 배포

### 리소스 제한 설정

```yaml
# docker-compose.yml
services:
  api:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G
        reservations:
          cpus: '1'
          memory: 2G
```

### 로그 로테이션

```yaml
services:
  api:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

### 환경별 설정

```bash
# 개발 환경
docker-compose -f docker-compose.yml up

# 프로덕션 환경
docker-compose -f docker-compose.prod.yml up -d
```

## 성능 비교

| 환경 | HWP 변환 방법 | 변환 시간 (10페이지) |
|-----|------------|------------------|
| **Windows (로컬)** | pyhwp (실패) | N/A |
| **Windows (로컬)** | LibreOffice | ~5초 |
| **Docker (Linux)** | hwp5txt | **~1초** ✅ |

**권장**: 프로덕션 환경은 Docker 사용

## 다음 단계

1. ✅ Docker 이미지 빌드
2. ✅ HWP 파일 업로드 테스트
3. ✅ RAG 질의응답 테스트
4. 📝 성능 벤치마크
5. 🚀 Kubernetes 배포 (선택적)

## 참고 문서

- [Dockerfile](Dockerfile): 이미지 정의
- [docker-compose.yml](docker-compose.yml): 서비스 구성
- [README.md](README.md): 전체 프로젝트 가이드
- [HWP_SOLUTION_ANALYSIS.md](HWP_SOLUTION_ANALYSIS.md): HWP 파서 분석
