# Streamlit UI 사용 가이드

## 개요

Streamlit UI는 문서 검색 및 RAG 시스템을 위한 사용자 친화적 웹 인터페이스입니다.

## 실행 방법

### 1. FastAPI 서버 시작 (백엔드)

```bash
uvicorn app.main:app --reload
```

### 2. Streamlit UI 시작 (프론트엔드)

```bash
streamlit run app/ui/streamlit_app.py
```

브라우저가 자동으로 열립니다: `http://localhost:8501`

## 주요 기능

### 1. 📤 파일 업로드

- **PDF 파일 업로드**: 로컬 PDF 파일 선택
- **청킹 전략 선택**:
  - `character_window`: 고정 크기 윈도우 (기본값)
  - `paragraph_based`: 문단 기반 청킹
  - `heading_based`: 제목 기반 청킹
- **파라미터 조정**:
  - 최대 청크 크기 (500~3000자)
  - 청크 겹침 (0~500자)
  - OCR 폴백 활성화/비활성화
- **처리 결과 시각화**:
  - 처리 상태 (OK/WARN/FAIL)
  - 청크 개수, 텍스트 길이
  - 청크 길이 분포 히스토그램
  - 통계 (평균, 최소, 최대, 표준편차)

### 2. 🔍 문서 검색

- **키워드 검색**: 자연어 질문 입력
- **검색 결과 개수 조정** (1~20개)
- **검색 결과 표시**:
  - 유사도 점수 (L2 거리, 낮을수록 유사)
  - 파일명, 청크 인덱스
  - 청킹 전략
  - 전체 텍스트 내용

### 3. 💬 질문하기 (RAG)

- **자연어 질문 입력**: 긴 질문 가능
- **파라미터 설정**:
  - 참조 문서 개수 (1~10개)
  - 최대 생성 토큰 (100~2000)
  - LLM 타입 선택:
    - `auto`: 자동 선택 (ENABLE_OPENAI 환경변수 기반)
    - `mock`: Mock LLM (개발/데모용, 항상 사용 가능)
    - `openai`: OpenAI GPT (ENABLE_OPENAI=true 필요)
- **답변 생성**:
  - LLM이 생성한 답변 표시
  - 사용된 LLM 타입 표시
  - 참조 문서 목록 (유사도 순)
  - 각 참조 문서의 상세 내용

### 4. ⚙️ 시스템 상태 (사이드바)

- **헬스체크**:
  - 시스템 상태: 🟢 healthy / 🟡 degraded / 🔴 unhealthy
  - 벡터 개수
  - 임베더 상태
  - 벡터스토어 상태
  - LLM 상태 및 타입
- **상태 새로고침** 버튼

## LLM 설정

### Mock LLM (기본값)

환경변수 설정 없이 바로 사용 가능합니다.

**특징**:
- 실제 LLM 호출 없음
- 검색된 청크 정보를 바탕으로 템플릿 기반 답변 생성
- 개발 및 데모에 적합

**답변 예시**:
```
[Mock LLM 답변]

질문: 구매 절차는?

검색된 문서 3개를 바탕으로 답변드립니다.

관련 내용 발췌:
구매업무처리규정에 따르면...

※ 참고: 이 답변은 Mock LLM이 생성한 것입니다.
실제 LLM을 사용하려면 ENABLE_OPENAI=true 환경변수를 설정하세요.

검색된 청크 개수: 3
평균 청크 길이: 850 자
```

### OpenAI LLM (dev-only)

실제 GPT 모델을 사용하려면:

#### 1단계: `.env` 파일 생성

```bash
cp .env.example .env
```

#### 2단계: `.env` 파일 수정

```bash
# OpenAI 활성화
ENABLE_OPENAI=true

# OpenAI API 키 입력
OPENAI_API_KEY=sk-your-actual-api-key-here

# 모델 선택 (선택사항)
OPENAI_MODEL=gpt-3.5-turbo
```

#### 3단계: openai 패키지 설치

```bash
pip install openai==1.12.0
```

#### 4단계: 서버 재시작

```bash
# FastAPI 서버 종료 후 재시작
uvicorn app.main:app --reload
```

**특징**:
- 실제 GPT-3.5/GPT-4 사용
- 문서 기반 정확한 답변 생성
- API 비용 발생 (주의!)

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `API_BASE_URL` | `http://localhost:8000` | FastAPI 서버 주소 |
| `ENABLE_OPENAI` | `false` | OpenAI 활성화 여부 |
| `OPENAI_API_KEY` | - | OpenAI API 키 |
| `OPENAI_MODEL` | `gpt-3.5-turbo` | OpenAI 모델명 |

## 사용 예시

### 예시 1: 문서 업로드 및 처리

1. "📤 파일 업로드" 탭 선택
2. PDF 파일 선택 (예: `구매업무처리규정.pdf`)
3. 청킹 전략: `heading_based` 선택
4. 최대 청크 크기: `1000` 설정
5. "파일 처리 시작" 버튼 클릭
6. 처리 완료 후 통계 확인

### 예시 2: 문서 검색

1. "🔍 문서 검색" 탭 선택
2. 질문 입력: "구매 요청 절차는?"
3. 검색 결과 개수: `5` 설정
4. "검색" 버튼 클릭
5. 유사도 순으로 정렬된 결과 확인

### 예시 3: RAG 질문-답변

1. "💬 질문하기" 탭 선택
2. 질문 입력: "구매업무처리규정에 따르면 구매 요청은 어떻게 해야 하나요?"
3. 참조 문서 개수: `5` 설정
4. LLM 타입: `auto` 선택
5. "답변 생성" 버튼 클릭
6. 생성된 답변 및 참조 문서 확인

## 트러블슈팅

### 문제: "Connection refused" 오류

**원인**: FastAPI 서버가 실행되지 않음

**해결**:
```bash
uvicorn app.main:app --reload
```

### 문제: "No relevant documents found" 오류

**원인**: 벡터스토어에 문서가 없음

**해결**: "📤 파일 업로드" 탭에서 PDF 파일을 먼저 업로드하세요

### 문제: OpenAI LLM 사용 불가

**원인**:
1. `ENABLE_OPENAI=false` 또는 미설정
2. `OPENAI_API_KEY` 미설정
3. `openai` 패키지 미설치

**해결**:
1. `.env` 파일 확인 및 수정
2. `pip install openai==1.12.0`
3. 서버 재시작

### 문제: 한글 폰트가 깨짐 (그래프)

**원인**: Windows에서 한글 폰트 설정 필요

**해결**: `streamlit_app.py`에 이미 포함됨 (`Malgun Gothic`)

## 아키텍처

```
┌─────────────────┐
│  Streamlit UI   │ (프론트엔드)
│  :8501          │
└────────┬────────┘
         │ HTTP
         ↓
┌─────────────────┐
│  FastAPI Server │ (백엔드)
│  :8000          │
└────────┬────────┘
         │
    ┌────┴────┬────────┬─────────┐
    ↓         ↓        ↓         ↓
 Parser   Chunker   Embedder   LLM
    ↓         ↓        ↓         ↓
  FAISS   Reports   Monitoring
```

## 향후 개선 사항

- [ ] 배치 파일 업로드
- [ ] 리포트 조회 및 관리 UI
- [ ] 청크 편집 기능
- [ ] 실시간 로그 스트리밍
- [ ] 사용자 인증
- [ ] 대시보드 및 통계 분석

## 참고

- FastAPI Swagger UI: http://localhost:8000/docs
- FastAPI ReDoc: http://localhost:8000/redoc
- Streamlit 문서: https://docs.streamlit.io
