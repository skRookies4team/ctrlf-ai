# Qwen3 임베딩 설정 가이드

## 📋 개요

이 가이드는 Qwen3 임베딩을 활성화하여 RAG 검색 품질을 개선하는 방법을 설명합니다.

### 현재 문제점 분석

1. **임베딩 문제**: Hash 기반 Dummy 임베딩 사용
   - 의미적 유사도가 아닌 해시 기반 pseudo-random 벡터
   - "구매"와 "구매업무처리규정"의 의미 연관성 파악 불가
   - 검색 결과가 무작위에 가까움

2. **청킹 문제**: character_window 전략 (기본값)
   - 1000자 단위로 텍스트를 자름
   - 문맥 무시 (헤딩, 섹션 경계 무시)
   - "구매 요청 절차"가 두 청크로 분리될 수 있음

3. **LLM 문제**: MockLLM 사용
   - 템플릿 기반 응답만 가능
   - 실제 언어 이해 없음

### 해결 방법

✅ **임베딩**: Qwen3/HuggingFace 임베딩 사용 (이 문서)
✅ **청킹**: heading_based 전략 사용 (Streamlit UI에서 설정)
⚠️ **LLM**: OpenAI API 사용 (선택사항, `.env` 설정)

---

## 🚀 STEP 1: 의존성 설치

Qwen3 임베딩을 사용하려면 다음 패키지가 필요합니다:

```bash
pip install langchain-community sentence-transformers torch
```

**참고**: `torch`는 CPU 버전이 설치됩니다 (GPU 없어도 작동).

### 설치 확인

```bash
python -c "from langchain_community.embeddings import HuggingFaceEmbeddings; print('✓ 설치 성공')"
```

---

## 🔧 STEP 2: 환경 변수 설정

### 2-1. `.env` 파일 생성

프로젝트 루트에 `.env` 파일이 없다면 생성:

```bash
cp .env.example .env
```

### 2-2. `.env` 파일 수정

`.env` 파일을 열어 다음과 같이 수정:

```bash
# ========================================
# 임베딩 설정
# ========================================

# Qwen3 임베딩 활성화
EMBEDDING_PROVIDER=qwen3

# 임베딩 차원 (기본: 384)
EMBEDDING_DIM=384

# Qwen3 모델 이름 (선택사항)
# 기본값: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
# QWEN3_MODEL_NAME=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

### 사용 가능한 EMBEDDING_PROVIDER 값

| 값 | 설명 | 의존성 | 성능 |
|---|---|---|---|
| `dummy` | Hash 기반 (기본값) | 없음 | ❌ 낮음 (의미 없음) |
| `qwen3` | Qwen3/HuggingFace | langchain-community, sentence-transformers | ✅ 높음 (의미 기반) |
| `openai` | OpenAI Embeddings | openai | ✅ 매우 높음 (유료) |

---

## 🔄 STEP 3: 기존 인덱스 삭제 및 재생성

**중요**: 임베딩 모델을 변경하면 기존 인덱스와 호환되지 않습니다!

### 3-1. 기존 FAISS 인덱스 삭제

```bash
# Windows
rmdir /s /q data\vector_store

# Linux/Mac
rm -rf data/vector_store
```

### 3-2. API 서버 재시작

```bash
# 기존 서버 종료 (Ctrl+C)

# 새로운 임베딩 설정으로 서버 시작
uvicorn app.main:app --reload
```

서버 로그에서 확인:

```
[Qwen3] Initializing model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
[Qwen3] Model loaded successfully
```

### 3-3. Streamlit UI 재시작

```bash
# 기존 Streamlit 종료 (Ctrl+C)

# Streamlit 재시작
streamlit run app/ui/streamlit_app.py
```

---

## 📤 STEP 4: 문서 재업로드 (heading_based 전략)

Streamlit UI에서 문서를 재업로드합니다.

### 4-1. "문서 업로드" 탭 이동

1. Streamlit UI 열기: http://localhost:8501
2. 사이드바에서 **"문서 업로드"** 선택

### 4-2. 청킹 전략 설정

- **청킹 전략**: `heading_based` 선택 ⭐
- **최대 청크 크기**: `2000` (기본값 1000보다 크게)
- **청크 겹침**: 200 (기본값)

**왜 heading_based?**
- 섹션/헤딩 경계를 존중
- "구매 요청 절차" 같은 논리적 단위가 하나의 청크로 유지됨
- 문맥 보존

### 4-3. 파일 업로드

"Choose File" 버튼 클릭 → PDF/HWP/DOCX/PPTX 파일 선택 → "업로드 및 처리" 클릭

### 4-4. 처리 결과 확인

처리 완료 후 다음 정보 확인:

- ✅ **상태**: SUCCESS
- ✅ **청크 수**: 적절한 개수 (너무 많지 않음)
- ✅ **임베딩**: "Qwen3" 로그 확인

예시 로그:

```
[Qwen3] Embedding 15 texts
[Qwen3] Successfully embedded 15 texts
```

---

## 🔍 STEP 5: 검색 테스트

### 5-1. "문서 검색" 탭에서 테스트

1. Streamlit UI에서 **"문서 검색"** 탭 이동
2. 검색어 입력: `구매`
3. Top-K: `5`
4. "검색" 버튼 클릭

### 5-2. 결과 분석

**Before (Dummy 임베딩)**:
- 유사도 점수: ~1.68 (무의미한 L2 거리)
- 결과: 무작위에 가까움
- "구매"와 관련 없는 문서가 상위에 표시됨

**After (Qwen3 임베딩)**:
- 유사도 점수: ~0.3-0.8 (의미 기반 거리)
- 결과: "구매", "구매업무처리규정" 등 관련 문서 상위 표시
- 문맥이 보존된 청크 반환

### 5-3. "질문하기" 탭에서 RAG 테스트 (선택사항)

**MockLLM 사용 시 (기본값)**:
- 검색 결과는 개선되지만, 응답은 템플릿 기반

**OpenAI LLM 사용 시** (`.env`에서 `ENABLE_OPENAI=true` 설정):
- 검색 결과 + 실제 GPT 응답
- 의미 있는 답변 생성

---

## 📊 STEP 6: 성능 평가 (선택사항)

실험 프레임워크를 사용하여 정량적 평가를 수행할 수 있습니다.

### 6-1. 평가 질문 준비

`experiments/embedding_eval/eval_questions.csv` 파일 편집:

```csv
question,expected_doc,expected_text
구매 요청 절차는?,구매업무처리규정,구매 요청서
계약 체결 방법은?,계약업무규정,계약 체결
```

### 6-2. Dummy 임베딩 인덱스 생성

```bash
EMBEDDING_PROVIDER=dummy python experiments/embedding_eval/build_indexes.py --provider dummy
```

### 6-3. Qwen3 임베딩 인덱스 생성

```bash
EMBEDDING_PROVIDER=qwen3 python experiments/embedding_eval/build_indexes.py --provider qwen3
```

### 6-4. 평가 실행

```bash
python experiments/embedding_eval/run_eval.py --providers dummy qwen3
```

### 6-5. 결과 해석

예상 결과:

```
================================================================================
임베딩 모델 성능 비교
================================================================================
Provider            Hit@1      Hit@3      Hit@5        MRR
--------------------------------------------------------------------------------
dummy               40.0%      60.0%      70.0%     0.5000
qwen3               80.0%      95.0%     100.0%     0.8750
================================================================================
```

- **Hit@1**: 1위가 정답인 비율 (dummy: 40% → qwen3: 80%)
- **MRR**: 평균 역순위 (높을수록 좋음)

---

## 🛠️ 트러블슈팅

### 문제 1: torch 설치 실패 (Windows)

**증상**:
```
ERROR: Could not find a version that satisfies the requirement torch
```

**해결**:
```bash
# CPU 버전 직접 설치
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
```

### 문제 2: langchain-community 버전 충돌

**증상**:
```
ERROR: Cannot install langchain-community
```

**해결**:
```bash
# 최신 버전 설치
pip install --upgrade langchain-community
```

### 문제 3: 모델 다운로드 느림

**증상**: 첫 실행 시 모델 다운로드에 시간이 걸림

**해결**:
- 정상입니다. `sentence-transformers` 모델이 HuggingFace Hub에서 다운로드됩니다 (~120MB)
- 두 번째 실행부터는 캐시된 모델 사용

**다운로드 위치** (Windows):
```
C:\Users\{사용자}\.cache\huggingface\hub\
```

### 문제 4: 검색 속도 느림

**증상**: Qwen3 사용 시 검색이 느려짐

**원인**: CPU에서 임베딩 생성

**해결**:
- GPU가 있다면: `model_kwargs={'device': 'cuda'}` (core/embedder.py 수정)
- GPU가 없다면: 속도는 느려도 품질이 훨씬 좋음 (trade-off)

### 문제 5: 기존 인덱스와 호환 안됨

**증상**:
```
RuntimeError: vector dimension mismatch
```

**해결**:
```bash
# 기존 인덱스 완전 삭제
rm -rf data/vector_store

# API 서버 재시작
# 문서 재업로드
```

---

## 📚 추가 자료

### Qwen3 임베딩 모델 변경 (고급)

`.env` 파일에서 다른 HuggingFace 모델 사용 가능:

```bash
# 다국어 지원 모델 (기본값)
QWEN3_MODEL_NAME=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

# 한국어 특화 모델 (예시)
# QWEN3_MODEL_NAME=jhgan/ko-sroberta-multitask

# 영어 전용 고성능 모델
# QWEN3_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
```

**주의**: 모델마다 벡터 차원이 다를 수 있음! 모델 변경 시 인덱스 재생성 필요.

### OpenAI 임베딩 사용 (고급)

`.env` 설정:

```bash
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-your-api-key-here
```

**장점**: 최고 품질
**단점**: 유료 (토큰당 과금)

---

## ✅ 체크리스트

설정 완료 확인:

- [ ] `pip install langchain-community sentence-transformers torch` 실행
- [ ] `.env` 파일에서 `EMBEDDING_PROVIDER=qwen3` 설정
- [ ] 기존 `data/vector_store/` 삭제
- [ ] API 서버 재시작 후 로그에서 `[Qwen3] Model loaded successfully` 확인
- [ ] Streamlit UI 재시작
- [ ] 문서 재업로드 (청킹 전략: `heading_based`)
- [ ] "문서 검색" 탭에서 테스트 → 관련 문서가 상위에 표시됨

---

## 🎯 기대 효과

### Before (Dummy + character_window)

- 검색 정확도: 30-40%
- 의미 이해: 없음
- 청킹 품질: 문맥 단절

### After (Qwen3 + heading_based)

- 검색 정확도: 80-90%
- 의미 이해: 한국어/영어 의미 기반 유사도
- 청킹 품질: 섹션 단위 문맥 보존

**검색 품질 2배 이상 개선 기대!** 🚀
