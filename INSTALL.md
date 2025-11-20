# 설치 가이드

## 환경 요구사항

- **Python**: 3.9 - 3.12 (3.13은 미지원)
- **OS**: Windows / Linux / macOS
- **RAM**: 최소 2GB (Qwen3 사용 시 4GB 권장)
- **Git**: 최신 버전 (2.x)

## 설치 단계

### 1. 저장소 클론

```bash
git clone https://github.com/skRookies4team/ctrlf-ai.git
cd ctrlf-ai
```

**⚠️ 중요**: Git 줄바꿈 설정 확인
```bash
# Windows 사용자
git config core.autocrlf true

# Linux/Mac 사용자
git config core.autocrlf input
```

### 2. Python 버전 확인

```bash
python --version
# 또는
python3 --version

# 예상 출력: Python 3.9.x ~ 3.12.x
```

**Python 3.13 사용 시 오류 발생 가능** → Python 3.12 사용 권장

### 3. 가상환경 생성

#### Windows

```bash
python -m venv venv
venv\Scripts\activate
```

#### Linux/Mac

```bash
python3 -m venv venv
source venv/bin/activate
```

### 4. pip 업그레이드

```bash
pip install --upgrade pip setuptools wheel
```

### 5. 의존성 설치

#### 방법 1: 필수 의존성만 (기본)

```bash
pip install -r requirements.txt
```

**설치 시간**: 약 2-3분

#### 방법 2: Qwen3 임베딩 포함 (권장)

```bash
# 필수 의존성
pip install -r requirements.txt

# Qwen3 임베딩 (CPU 버전)
pip install langchain-huggingface sentence-transformers torch --index-url https://download.pytorch.org/whl/cpu
```

**설치 시간**: 약 5-10분

### 6. 환경변수 설정

```bash
# .env.example 복사
cp .env.example .env

# 편집
# Windows: notepad .env
# Linux/Mac: nano .env
```

**최소 설정**:
```bash
EMBEDDING_PROVIDER=dummy  # 또는 qwen3
ENABLE_OPENAI=false
```

**Qwen3 사용 시**:
```bash
EMBEDDING_PROVIDER=qwen3
EMBEDDING_DIM=384
```

**OpenAI 사용 시**:
```bash
ENABLE_OPENAI=true
OPENAI_API_KEY=sk-proj-your-api-key-here
OPENAI_MODEL=gpt-3.5-turbo
```

### 7. 설치 확인

```bash
# Python 패키지 확인
python -c "import fastapi, pdfplumber, faiss, numpy; print('✅ All dependencies installed')"

# 예상 출력: ✅ All dependencies installed
```

### 8. 서버 실행

```bash
# FastAPI 서버 실행
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 예상 출력:
# INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

**접속**: http://localhost:8000/docs

## 일반적인 설치 오류

### 1. faiss-cpu 버전 오류

**증상**:
```
ERROR: Could not find a version that satisfies the requirement faiss-cpu==1.7.4
```

**원인**: 구버전 requirements.txt

**해결**:
```bash
# requirements.txt 확인
grep faiss requirements.txt

# 예상 출력: faiss-cpu>=1.8.0 (올바름)
# 만약 1.7.4로 되어있으면 수정 필요

# 수정 후 재설치
pip install --upgrade faiss-cpu
```

### 2. numpy 버전 충돌

**증상**:
```
ERROR: numpy 2.0.0 is not compatible with faiss-cpu
```

**해결**:
```bash
pip install "numpy>=1.24.3,<2.0.0"
```

### 3. torch 설치 실패

**증상**:
```
ERROR: torch requires CUDA
```

**해결**: CPU 버전 명시
```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### 4. pdfplumber 설치 실패

**증상**:
```
ERROR: Could not build wheels for pdfplumber
```

**해결**: 시스템 패키지 먼저 설치

**Ubuntu/Debian**:
```bash
sudo apt-get install python3-dev libpoppler-dev
pip install pdfplumber
```

**macOS**:
```bash
brew install poppler
pip install pdfplumber
```

**Windows**: 대부분 정상 설치됨

### 5. 줄바꿈 오류 (Linux/Mac)

**증상**:
```
SyntaxError: invalid character in identifier
```

**원인**: CRLF 줄바꿈 문제

**해결**:
```bash
# Git 재설정
git config core.autocrlf input
git rm --cached -r .
git reset --hard

# 또는 수동 변환 (Linux/Mac)
find . -name "*.py" -exec dos2unix {} \;
```

### 6. 파일 인코딩 오류

**증상**:
```
UnicodeDecodeError: 'utf-8' codec can't decode
```

**해결**:
```bash
# 파일 인코딩 확인
file -i core/*.py

# 모두 utf-8이어야 함
# 다르면 재클론 필요
rm -rf ctrlf-ai
git clone https://github.com/skRookies4team/ctrlf-ai.git
```

## Python 버전별 호환성

| Python 버전 | FAISS | NumPy | Torch | 상태 |
|-----------|-------|-------|-------|------|
| 3.9 | ✅ | ✅ | ✅ | 권장 |
| 3.10 | ✅ | ✅ | ✅ | 권장 |
| 3.11 | ✅ | ✅ | ✅ | 권장 |
| 3.12 | ✅ | ✅ | ✅ | **권장** |
| 3.13 | ⚠️ | ⚠️ | ❌ | 미지원 |

**Python 3.13 사용자**: Python 3.12로 다운그레이드 필요

## 운영체제별 가이드

### Windows 10/11

```bash
# 1. Python 3.12 설치 (Microsoft Store 또는 python.org)
# 2. Git 설치 (git-scm.com)
# 3. 저장소 클론
git clone https://github.com/skRookies4team/ctrlf-ai.git
cd ctrlf-ai

# 4. 가상환경
python -m venv venv
venv\Scripts\activate

# 5. 의존성 설치
pip install -r requirements.txt
```

### Ubuntu 20.04/22.04

```bash
# 1. Python 3.12 설치
sudo apt update
sudo apt install python3.12 python3.12-venv python3-pip

# 2. 시스템 패키지 설치
sudo apt install libpoppler-dev python3-dev

# 3. 저장소 클론
git clone https://github.com/skRookies4team/ctrlf-ai.git
cd ctrlf-ai

# 4. 가상환경
python3.12 -m venv venv
source venv/bin/activate

# 5. 의존성 설치
pip install -r requirements.txt
```

### macOS

```bash
# 1. Homebrew 설치 (https://brew.sh)
# 2. Python 3.12 설치
brew install python@3.12

# 3. 시스템 패키지 설치
brew install poppler

# 4. 저장소 클론
git clone https://github.com/skRookies4team/ctrlf-ai.git
cd ctrlf-ai

# 5. 가상환경
python3.12 -m venv venv
source venv/bin/activate

# 6. 의존성 설치
pip install -r requirements.txt
```

## Docker 사용 (가장 쉬운 방법)

```bash
# 1. Docker Desktop 설치
# 2. 저장소 클론
git clone https://github.com/skRookies4team/ctrlf-ai.git
cd ctrlf-ai

# 3. Docker 빌드 및 실행
docker-compose up -d

# 4. 접속
# FastAPI: http://localhost:8000/docs
# Streamlit: http://localhost:8501
```

**장점**:
- 의존성 자동 설치
- 환경 독립적
- HWP 변환 (hwp5txt) 자동 지원

## 설치 검증 체크리스트

- [ ] Python 버전 확인 (3.9 - 3.12)
- [ ] Git 클론 성공
- [ ] 가상환경 활성화
- [ ] `pip install -r requirements.txt` 성공
- [ ] `python -c "import fastapi, faiss"` 성공
- [ ] `.env` 파일 생성 및 설정
- [ ] `uvicorn app.main:app --reload` 실행 성공
- [ ] http://localhost:8000/docs 접속 성공

## 다음 단계

설치 완료 후:
1. [README.md](README.md): 사용 방법
2. [DOCKER.md](DOCKER.md): Docker 실행 (권장)
3. [HWP_TEST_GUIDE.md](HWP_TEST_GUIDE.md): HWP 파일 테스트

## 문의

설치 중 문제 발생 시:
1. [GitHub Issues](https://github.com/skRookies4team/ctrlf-ai/issues) 등록
2. Python 버전, OS, 오류 메시지 포함

---

**업데이트**: 2025-01-20
**작성자**: Claude Code
