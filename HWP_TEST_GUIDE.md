# HWP 파일 테스트 가이드

## ✅ 완료된 작업

1. **HWP 변환 어댑터 생성** (`core/hwp_converter.py`)
   - 3가지 변환 방법 자동 선택
   - 세희 코드(hwp5txt) 완전 통합

2. **Parser 연결** (`core/parser.py`)
   - `extract_text_from_hwp()`에서 어댑터 호출
   - 확장자 기반 자동 라우팅

3. **Docker 환경 구축**
   - Dockerfile: hwp5txt 자동 설치
   - docker-compose.yml: 원클릭 실행

## 🚀 테스트 방법

### 방법 1: Docker 사용 (권장 - hwp5txt 사용)

#### 1단계: Docker 이미지 빌드

```bash
cd "C:\Users\user\OneDrive\바탕 화면\최종프로젝트\CTRL_F\AI\chunking"

# 이미지 빌드 (5-10분 소요)
docker-compose build
```

#### 2단계: 컨테이너 실행

```bash
# 백그라운드 실행
docker-compose up -d

# 로그 확인
docker-compose logs -f api
```

**접속**:
- FastAPI: http://localhost:8000/docs
- Streamlit UI: http://localhost:8501

#### 3단계: HWP 변환 확인

```bash
# hwp5txt 설치 확인
docker-compose exec api hwp5txt --version

# 예상 출력: hwp5 0.x.x
```

#### 4단계: HWP 파일 업로드

**Streamlit UI 사용**:
1. http://localhost:8501 접속
2. "문서 업로드" 탭
3. `data/` 폴더의 HWP 파일 선택
4. 청킹 전략: `heading_based`
5. "처리 시작" 클릭

**cURL 사용**:
```bash
# HWP 파일 업로드
curl -X POST "http://localhost:8000/api/v1/ingest/file" \
  -F "file=@data/구매업무처리규정.hwp" \
  -F "chunk_strategy=heading_based" \
  -F "max_chars=2000"
```

**성공 응답**:
```json
{
  "ingest_id": "...",
  "file_name": "구매업무처리규정.hwp",
  "status": "OK",
  "num_chunks": 15
}
```

#### 5단계: 로그 확인

```bash
docker-compose logs -f api | grep HWP

# 예상 로그:
# INFO: [HWP] Extracting text from: 구매업무처리규정.hwp
# INFO: Using hwp5txt (preferred method)
# INFO: [hwp5txt] Converting HWP: 구매업무처리규정.hwp
# INFO: [hwp5txt] Extracted 15234 chars from 구매업무처리규정.hwp
```

---

### 방법 2: Windows 로컬 (LibreOffice 사용)

#### 1단계: LibreOffice 설치

```powershell
# PowerShell 관리자 권한
choco install libreoffice

# 환경변수 추가
$env:PATH += ";C:\Program Files\LibreOffice\program"
```

#### 2단계: 서버 실행

```bash
# FastAPI 서버
uvicorn app.main:app --reload

# 별도 터미널: Streamlit UI
streamlit run app/ui/streamlit_app.py
```

#### 3단계: HWP 파일 테스트

**테스트 스크립트 사용**:
```bash
python test_hwp_converter.py data/구매업무처리규정.hwp
```

**예상 출력**:
```
======================================================================
HWP 변환 어댑터 테스트
======================================================================

[1] 사용 가능한 변환 방법:
  ✅ libreoffice

[2] 권장 변환 방법: libreoffice

[3] HWP 파일 변환 테스트:
  파일: 구매업무처리규정.hwp
  크기: 125.3 KB

✅ 변환 성공!
  추출된 텍스트 길이: 15,234 자
  줄 수: 456 줄

  미리보기 (처음 500자):
  ------------------------------------------------------------
  구매업무처리규정

  제 1 장 총칙

  제 1 조 (목적)
  이 규정은 구매업무의 효율적인 처리를 위하여...
  ------------------------------------------------------------
```

---

## 📊 성능 비교

| 환경 | 변환 방법 | 10페이지 HWP | 상태 |
|-----|---------|------------|------|
| **Docker (Linux)** | hwp5txt | ~1초 | ✅ 권장 |
| **Windows** | LibreOffice | ~5초 | ✅ 가능 |
| **Windows** | pyhwp | 실패 | ❌ 지원 안됨 |

---

## 🔍 문제 해결

### 1. Docker - hwp5txt not found

**증상**:
```
ERROR: [HWP] All HWP conversion methods failed
```

**해결**:
```bash
# 이미지 재빌드
docker-compose build --no-cache

# hwp5 설치 확인
docker-compose exec api which hwp5txt
```

### 2. Windows - LibreOffice not found

**증상**:
```
ERROR: soffice (LibreOffice) not found
```

**해결**:
```bash
# LibreOffice 설치 확인
soffice --version

# 환경변수 추가 (PowerShell)
$env:PATH += ";C:\Program Files\LibreOffice\program"
```

### 3. HWP 변환은 되는데 텍스트가 이상함

**증상**: 깨진 문자 또는 빈 텍스트

**해결**:
```bash
# 1. HWP 파일 인코딩 확인
# 2. hwp5txt 직접 테스트
docker-compose exec api hwp5txt /app/data/file.hwp

# 3. LibreOffice 직접 테스트
soffice --headless --convert-to txt --outdir . file.hwp
```

---

## 📝 테스트 체크리스트

### Docker 환경

- [ ] `docker-compose build` 성공
- [ ] `docker-compose up` 실행
- [ ] `hwp5txt --version` 확인
- [ ] HWP 파일 업로드 (Streamlit UI)
- [ ] 로그에서 `[hwp5txt] Extracted X chars` 확인
- [ ] 문서 검색 탭에서 검색 성공
- [ ] RAG 질문하기 탭에서 답변 생성 성공

### Windows 로컬 환경

- [ ] LibreOffice 설치
- [ ] `soffice --version` 확인
- [ ] `python test_hwp_converter.py` 성공
- [ ] FastAPI 서버 실행
- [ ] HWP 파일 업로드
- [ ] 로그에서 `[LibreOffice] Extracted X chars` 확인

---

## 🎯 다음 단계

1. **성능 벤치마크**
   ```bash
   # 여러 HWP 파일로 처리 시간 측정
   time docker-compose exec api python -c "
   from core.hwp_converter import convert_hwp_to_text
   convert_hwp_to_text('data/구매업무처리규정.hwp')
   "
   ```

2. **대용량 파일 테스트**
   - 100페이지 이상 HWP 파일
   - 메모리 사용량 모니터링

3. **에러 핸들링 개선**
   - 손상된 HWP 파일 처리
   - 타임아웃 설정 최적화

4. **프로덕션 배포**
   - Kubernetes 배포
   - 로드 밸런싱
   - 모니터링 (Prometheus + Grafana)

---

## 📖 참고 문서

- [DOCKER.md](DOCKER.md): Docker 상세 가이드
- [HWP_SOLUTION_ANALYSIS.md](HWP_SOLUTION_ANALYSIS.md): HWP 파서 분석
- [README.md](README.md): 프로젝트 전체 가이드

---

**작성일**: 2025-01-20
**작성자**: Claude Code
**Git 커밋**: `d8019b1` (feat: HWP 텍스트 변환 어댑터 및 Docker 지원 추가)
