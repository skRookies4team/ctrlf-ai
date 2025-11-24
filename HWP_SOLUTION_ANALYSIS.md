# HWP 파서 솔루션 분석 및 적용 가능성

## 현재 상황

### 우리 프로젝트 (CTRL-F AI)
```python
# core/parser.py:17-24
try:
    import pyhwp
    HWP_AVAILABLE = True
except ImportError:
    HWP_AVAILABLE = False
    logger.warning("pyhwp not installed. HWP files will be skipped.")

def extract_text_from_hwp(hwp_path: str) -> str:
    if not HWP_AVAILABLE:
        logger.warning(f"pyhwp not installed. Skipping HWP file: {hwp_path}")
        return ""
    # ... pyhwp 사용 시도 (실패)
```

**문제점**:
- `pyhwp` 라이브러리 설치 실패 (Python 2 호환성 문제)
- HWP 파일 업로드 시 빈 문자열 반환
- 한국 공공기관 문서 90%가 HWP → 치명적

---

## 세희 코드 분석 (prompt.txt)

### 사용 기술: `hwp5txt` CLI 도구

```python
# prompt.txt:65-76
def convert_hwp_to_text(hwp_path: Path) -> str:
    hwp_path = hwp_path.resolve()
    if not hwp_path.exists():
        raise FileNotFoundError(hwp_path)

    result = subprocess.run(
        ["hwp5txt", str(hwp_path)],  # CLI 명령어 실행
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout
```

### 핵심 차이점

| 항목 | 우리 프로젝트 (pyhwp) | 세희 코드 (hwp5txt) |
|-----|---------------------|-------------------|
| **방식** | Python 라이브러리 import | CLI 도구 subprocess 호출 |
| **패키지** | `pyhwp` (Python 2 전용) | `hwp5` (Python 3 호환) |
| **설치** | `pip install pyhwp` (실패) | `pip install hwp5` |
| **실행** | `pyhwp.HWPDocument()` | `subprocess.run(["hwp5txt", ...])` |
| **OS 제약** | Linux/Mac (Python 2) | Linux/Mac (Python 3) |
| **Windows** | ❌ 불가 | ❌ 불가 (hwp5txt 없음) |

---

## hwp5 패키지 조사

### 1. hwp5 라이브러리

**PyPI**: https://pypi.org/project/hwp5/

```bash
pip install hwp5
```

**제공 도구**:
- `hwp5txt`: HWP → 텍스트 변환 (CLI)
- `hwp5html`: HWP → HTML 변환
- `hwp5proc`: HWP 구조 분석

**장점**:
- ✅ Python 3 호환
- ✅ 활발히 관리됨 (최근 업데이트: 2023)
- ✅ CLI 도구로 안정적

**단점**:
- ❌ **Linux/Mac 전용** (Windows 미지원)
- ❌ 시스템 의존성: `libhwp` (한컴오피스 라이브러리)

---

## 현재 환경 확인

### 시스템 정보
- **OS**: Windows (MINGW64_NT-10.0-26100)
- **Python**: 3.12.7
- **hwp5txt**: ❌ 설치 안됨 (which hwp5txt 실패)
- **pyhwp**: ❌ 설치 안됨 (pip list 결과 없음)

### 결론
**세희 코드는 현재 Windows 환경에서 사용 불가**

---

## 적용 가능성 분석

### ✅ Linux/WSL 환경에서는 가능

세희가 "리눅스에서 하면 된다"고 한 이유:

```bash
# Ubuntu/Debian
sudo apt-get install hwp5
pip install hwp5

# 사용
hwp5txt 구매업무처리규정.hwp
```

**예상 결과**:
```
제 1 조 (목적)
이 규정은 구매업무의 효율적인 처리를 위하여...
```

### ❌ Windows 환경에서는 불가

**이유**:
1. `hwp5` 패키지가 Windows 미지원
2. `libhwp` 시스템 라이브러리가 Linux 전용
3. `hwp5txt` CLI 도구가 설치 안됨

---

## 우리 프로젝트 적용 방안

### 방안 1: Docker Linux 컨테이너 (✅ 추천)

**장점**:
- Windows에서도 Linux 환경 실행 가능
- 세희 코드를 그대로 사용 가능
- 배포 시 OS 독립적

**구현**:

```dockerfile
# Dockerfile
FROM python:3.12-slim

# hwp5 설치
RUN apt-get update && \
    apt-get install -y hwp5 && \
    pip install hwp5

# 애플리케이션 코드
COPY . /app
WORKDIR /app
RUN pip install -r requirements.txt

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```python
# core/parser.py 수정
def extract_text_from_hwp(hwp_path: str) -> str:
    """hwp5txt CLI 도구 사용 (세희 방식)"""
    try:
        result = subprocess.run(
            ["hwp5txt", hwp_path],
            capture_output=True,
            text=True,
            check=True,
            timeout=30  # 30초 타임아웃
        )
        return result.stdout
    except FileNotFoundError:
        logger.error("hwp5txt not found. Install hwp5 package.")
        return ""
    except subprocess.CalledProcessError as e:
        logger.error(f"hwp5txt failed: {e.stderr}")
        return ""
    except subprocess.TimeoutExpired:
        logger.error("hwp5txt timeout")
        return ""
```

**실행**:
```bash
# Docker 빌드
docker build -t ctrl-f-ai .

# 실행
docker run -p 8000:8000 ctrl-f-ai
```

---

### 방안 2: WSL2 (Windows Subsystem for Linux)

**장점**:
- Docker 없이 Linux 환경 사용
- 개발 편의성

**구현**:

```bash
# WSL2 설치 (PowerShell 관리자 권한)
wsl --install

# Ubuntu 실행
wsl

# hwp5 설치
sudo apt-get update
sudo apt-get install -y python3-pip hwp5
pip install hwp5

# 프로젝트 실행
cd /mnt/c/Users/user/OneDrive/바탕\ 화면/최종프로젝트/CTRL_F/AI/chunking
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**단점**:
- 파일 시스템 경로 변환 필요 (Windows → WSL)
- 성능 오버헤드

---

### 방안 3: 온라인 변환 API (💰 유료/제한적)

**서비스**:
- CloudConvert API: https://cloudconvert.com/api
- Convertio API: https://convertio.co/api/

**구현**:

```python
import requests

def extract_text_from_hwp_api(hwp_path: str) -> str:
    """CloudConvert API로 HWP → TXT 변환"""
    API_KEY = os.getenv("CLOUDCONVERT_API_KEY")

    # 1. HWP 업로드
    response = requests.post(
        "https://api.cloudconvert.com/v2/import/upload",
        headers={"Authorization": f"Bearer {API_KEY}"},
        files={"file": open(hwp_path, "rb")}
    )
    task_id = response.json()["data"]["id"]

    # 2. 변환 요청 (HWP → TXT)
    response = requests.post(
        f"https://api.cloudconvert.com/v2/convert",
        json={
            "input": task_id,
            "output_format": "txt"
        }
    )

    # 3. 결과 다운로드
    download_url = response.json()["data"]["result"]["files"][0]["url"]
    text = requests.get(download_url).text
    return text
```

**단점**:
- ❌ 비용 발생 (무료 플랜 제한적)
- ❌ 외부 의존성
- ❌ 개인정보 유출 위험 (문서 업로드)

---

### 방안 4: LibreOffice CLI (🆓 무료, 크로스 플랫폼)

**장점**:
- Windows/Linux/Mac 모두 지원
- 무료 오픈소스
- HWP 읽기 지원 (한컴 필터 포함)

**설치**:

```bash
# Windows
choco install libreoffice

# Linux
sudo apt-get install libreoffice

# Mac
brew install libreoffice
```

**구현**:

```python
def extract_text_from_hwp_libreoffice(hwp_path: str) -> str:
    """LibreOffice CLI로 HWP → TXT 변환"""
    import tempfile

    # 임시 출력 디렉토리
    with tempfile.TemporaryDirectory() as tmpdir:
        # HWP → TXT 변환
        result = subprocess.run(
            [
                "soffice",  # LibreOffice CLI
                "--headless",  # GUI 없이
                "--convert-to", "txt",
                "--outdir", tmpdir,
                hwp_path
            ],
            capture_output=True,
            text=True,
            timeout=60
        )

        # 변환된 TXT 파일 읽기
        txt_file = Path(tmpdir) / f"{Path(hwp_path).stem}.txt"
        if txt_file.exists():
            return txt_file.read_text(encoding="utf-8")
        else:
            logger.error(f"LibreOffice conversion failed: {result.stderr}")
            return ""
```

**테스트**:

```bash
# Windows에서 테스트
soffice --headless --convert-to txt --outdir . 구매업무처리규정.hwp
```

**장점**:
- ✅ Windows에서 바로 사용 가능 (Docker 불필요)
- ✅ 무료
- ✅ 크로스 플랫폼

**단점**:
- ⚠️ LibreOffice 설치 필요 (약 300MB)
- ⚠️ 변환 품질이 `hwp5txt`보다 낮을 수 있음

---

## 최종 권장 방안

### 🥇 1순위: Docker + hwp5txt (세희 방식)

**이유**:
- 세희 코드를 거의 그대로 사용 가능
- 배포 시 OS 독립적 (프로덕션 환경에서도 동일)
- Linux 환경에서 검증된 `hwp5` 패키지 사용

**적용 코드**:

```python
# core/parser.py
import subprocess
from pathlib import Path

def extract_text_from_hwp(hwp_path: str) -> str:
    """
    HWP 파일에서 텍스트 추출 (hwp5txt CLI 사용)

    ⚠️ 세희 코드에서 가져옴 (prompt.txt:65-76)
    ⚠️ hwp5 패키지 필요: pip install hwp5
    ⚠️ Linux 환경 필요 (Docker 또는 WSL2)

    Args:
        hwp_path: HWP 파일 경로

    Returns:
        str: 추출된 텍스트
    """
    hwp_path = Path(hwp_path).resolve()

    if not hwp_path.exists():
        logger.error(f"HWP file not found: {hwp_path}")
        return ""

    try:
        result = subprocess.run(
            ["hwp5txt", str(hwp_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30  # 30초 타임아웃
        )

        text = result.stdout
        logger.info(f"[hwp5txt] Extracted {len(text)} chars from {hwp_path.name}")
        return text

    except FileNotFoundError:
        logger.error("hwp5txt not found. Install: pip install hwp5 (Linux only)")
        return ""

    except subprocess.CalledProcessError as e:
        logger.error(f"hwp5txt failed: {e.stderr}")
        return ""

    except subprocess.TimeoutExpired:
        logger.error(f"hwp5txt timeout (>30s) for {hwp_path.name}")
        return ""
```

**Dockerfile**:

```dockerfile
FROM python:3.12-slim

# hwp5 시스템 패키지 설치
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        hwp5 \
        tesseract-ocr \
        poppler-utils && \
    rm -rf /var/lib/apt/lists/*

# Python 패키지 설치
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install hwp5

# 애플리케이션 코드
COPY . .

# 포트 노출
EXPOSE 8000

# 실행
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

### 🥈 2순위: LibreOffice CLI (Windows 개발 환경)

**이유**:
- 로컬 개발 시 Docker 없이 바로 테스트 가능
- Windows에서 즉시 사용 가능

**적용 방법**:

```bash
# LibreOffice 설치
choco install libreoffice

# 환경변수 추가 (PowerShell)
$env:PATH += ";C:\Program Files\LibreOffice\program"

# 테스트
soffice --version
```

---

## 비교표: 4가지 방안

| 방안 | Windows 지원 | Linux 지원 | 품질 | 비용 | 설치 복잡도 | 세희 코드 호환 |
|-----|------------|-----------|------|------|-----------|-------------|
| **Docker + hwp5txt** | ✅ (컨테이너) | ✅ | ⭐⭐⭐⭐⭐ | 무료 | 중간 | ✅ 100% |
| **WSL2 + hwp5txt** | ✅ (WSL) | ✅ | ⭐⭐⭐⭐⭐ | 무료 | 높음 | ✅ 100% |
| **CloudConvert API** | ✅ | ✅ | ⭐⭐⭐⭐ | 유료 | 낮음 | ❌ |
| **LibreOffice CLI** | ✅ | ✅ | ⭐⭐⭐ | 무료 | 낮음 | 부분 |

---

## 구현 우선순위

### Phase 1: LibreOffice로 빠른 검증 (1일)

```python
# core/parser.py에 LibreOffice 함수 추가
def extract_text_from_hwp(hwp_path: str) -> str:
    # 1순위: LibreOffice 시도
    text = extract_text_from_hwp_libreoffice(hwp_path)
    if text:
        return text

    # 2순위: 빈 문자열 (graceful fallback)
    logger.warning("HWP extraction failed")
    return ""
```

**목표**: HWP 파일 업로드 시 최소한 텍스트 추출되는지 확인

### Phase 2: Docker로 프로덕션 준비 (3일)

```bash
# Dockerfile 작성
# docker-compose.yml 작성
# CI/CD 파이프라인 연동
```

**목표**: 배포 환경에서 안정적으로 hwp5txt 사용

---

## 결론

### ✅ 세희 코드 적용 가능

**조건**:
- Docker 또는 WSL2 환경 필요
- `hwp5` 패키지 설치 필요

### 📝 즉시 적용 가능한 코드

```python
# core/parser.py에 추가
import subprocess

def extract_text_from_hwp(hwp_path: str) -> str:
    """세희 방식: hwp5txt CLI 사용"""
    try:
        result = subprocess.run(
            ["hwp5txt", str(hwp_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )
        return result.stdout
    except Exception as e:
        logger.error(f"hwp5txt failed: {e}")
        return ""
```

### 🚀 권장 실행 방법

**개발 환경** (Windows):
```bash
# LibreOffice 설치 후 사용
choco install libreoffice
```

**프로덕션 환경**:
```bash
# Docker 컨테이너로 실행
docker-compose up
```

---

**다음 단계**: LibreOffice 또는 Docker 중 선택하여 구현할까요?
