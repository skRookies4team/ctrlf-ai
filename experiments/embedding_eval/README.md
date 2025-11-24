# 임베딩 모델 평가 프레임워크

다양한 임베딩 모델의 검색 성능을 비교하기 위한 실험 프레임워크

## 📁 디렉토리 구조

```
experiments/embedding_eval/
├── README.md               # 이 파일
├── eval_questions.csv      # 평가 질문 템플릿
├── build_indexes.py        # FAISS 인덱스 생성 스크립트
├── run_eval.py             # 평가 실행 스크립트
└── indexes/                # 생성된 인덱스 (자동 생성)
    ├── dummy/
    │   ├── faiss.index
    │   └── metadata.jsonl
    ├── qwen_06b/
    │   ├── faiss.index
    │   └── metadata.jsonl
    └── qwen_15b/
        ├── faiss.index
        └── metadata.jsonl
```

## 🚀 사용 방법

### 1단계: 인덱스 생성

각 임베딩 모델에 대해 별도 인덱스를 생성합니다.

```bash
# Dummy 임베딩 (기본)
python experiments/embedding_eval/build_indexes.py --provider dummy

# Qwen 0.6B (향후 지원)
python experiments/embedding_eval/build_indexes.py --provider qwen_06b

# Qwen 1.5B (향후 지원)
python experiments/embedding_eval/build_indexes.py --provider qwen_15b
```

**옵션**:
- `--data-dir`: 문서 디렉토리 (기본: `data/files`)
- `--output-dir`: 인덱스 출력 디렉토리 (기본: 자동 생성)
- `--chunk-strategy`: 청킹 전략 (character_window, paragraph_based, heading_based)
- `--max-chars`: 최대 청크 크기 (기본: 1000)
- `--overlap-chars`: 청크 겹침 (기본: 200)

### 2단계: 평가 질문 준비

`eval_questions.csv` 파일을 수정하여 평가 질문을 추가합니다.

**형식**:
```csv
question,expected_doc,expected_text
구매 요청 절차는?,구매업무처리규정,구매 요청서
```

- `question`: 평가할 질문
- `expected_doc`: 정답 문서명 (일부 포함 가능)
- `expected_text`: 정답 텍스트 (현재 미사용, 향후 확장용)

### 3단계: 평가 실행

여러 모델에 대해 동일한 질문으로 평가를 실행합니다.

```bash
# 단일 모델 평가
python experiments/embedding_eval/run_eval.py --providers dummy

# 여러 모델 비교
python experiments/embedding_eval/run_eval.py --providers dummy qwen_06b qwen_15b

# 결과를 JSON 파일로 저장
python experiments/embedding_eval/run_eval.py --providers dummy qwen_06b --output results.json
```

**옵션**:
- `--providers`: 평가할 제공자 리스트
- `--questions`: 질문 CSV 파일 경로 (기본: `experiments/embedding_eval/eval_questions.csv`)
- `--indexes-dir`: 인덱스 디렉토리 (기본: `experiments/embedding_eval/indexes`)
- `--top-k`: Top-K 평가 (기본: 5)
- `--output`: 결과를 저장할 JSON 파일 경로 (선택)

## 📊 평가 지표

### Hit@k
상위 k개 결과에 정답 문서가 포함되는 비율

- **Hit@1**: 1위가 정답인 비율
- **Hit@3**: 상위 3개 안에 정답이 있는 비율
- **Hit@5**: 상위 5개 안에 정답이 있는 비율

### MRR (Mean Reciprocal Rank)
정답의 평균 역순위

- 1위에 정답: 1.0
- 2위에 정답: 0.5
- 3위에 정답: 0.333...
- 정답 없음: 0.0

**해석**: 높을수록 정답이 상위에 랭크됨

## 📈 결과 예시

```
================================================================================
임베딩 모델 성능 비교
================================================================================
Provider            Hit@1      Hit@3      Hit@5        MRR
--------------------------------------------------------------------------------
dummy               60.0%      80.0%      90.0%     0.7000
qwen_06b            75.0%      90.0%     100.0%     0.8250
qwen_15b            80.0%      95.0%     100.0%     0.8750
================================================================================
```

## 🛠️ 확장 방법

### 새로운 임베딩 모델 추가

1. `core/embedder.py`에 새 임베딩 클래스 추가
2. `build_indexes.py`의 `choices`에 provider 이름 추가
3. 인덱스 생성 및 평가 실행

예시:
```python
# core/embedder.py
class Qwen06BEmbedder:
    def __init__(self):
        from langchain_qwen3 import Qwen3Embeddings
        self.model = Qwen3Embeddings(model_name="Qwen/Qwen3-Embedding-0.6B")

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return [self.model.embed_query(text) for text in texts]
```

### 새로운 평가 질문 추가

`eval_questions.csv`에 행을 추가하면 됩니다.

```csv
question,expected_doc,expected_text
새로운 질문은?,예상 문서명,예상 텍스트
```

### 평가 지표 추가

`run_eval.py`의 `evaluate_provider()` 함수를 수정하여 새로운 지표를 추가할 수 있습니다.

예시: Precision@k, Recall@k, NDCG 등

## 📝 주의사항

### ⚠️ 운영 코드와 분리
- 이 실험 프레임워크는 **운영 API를 수정하지 않습니다**
- `core/` 모듈을 import만 사용
- 별도 인덱스 파일 생성 (운영 인덱스와 분리)

### ⚠️ 동일한 전처리 사용
- 모든 모델이 **동일한 청킹 전략** 사용
- 공정한 비교를 위해 전처리는 고정

### ⚠️ GPU 없어도 작동
- Dummy 임베딩은 GPU 불필요
- Qwen 모델은 CPU에서도 작동 (느림)

## 🔍 트러블슈팅

### 인덱스를 찾을 수 없음
```
Index directory not found: experiments/embedding_eval/indexes/qwen_06b
```

**해결**: 먼저 `build_indexes.py`로 인덱스를 생성하세요.

### 문서가 없음
```
No files found in data/files
```

**해결**: `data/files/` 디렉토리에 PDF 또는 HWP 파일을 추가하세요.

### 임베딩 모델 로딩 실패
```
Failed to load Qwen model
```

**해결**:
1. 필요한 패키지 설치 확인
2. `core/embedder.py`에 해당 모델 구현 확인

## 📚 참고

- CTRLF-AI 메인 문서: `../../README.md`
- 테스트 가이드: `../../TESTING.md`
- Streamlit UI 가이드: `../../STREAMLIT_UI.md`
