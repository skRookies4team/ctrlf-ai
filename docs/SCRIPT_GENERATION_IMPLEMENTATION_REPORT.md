# 교육 영상 스크립트 자동 생성 시스템 구현 보고서

## 1. 시스템 개요

### 1.1 목적
법정의무교육 문서를 기반으로 교육 영상 스크립트를 자동 생성하는 시스템입니다. 문서를 통째로 LLM에 전달하지 않고, **씬 단위 RAG(Retrieval-Augmented Generation)** 방식을 사용하여 LLM 컨텍스트 제한(8K 토큰)을 우회합니다.

### 1.2 핵심 기술
- **씬 단위 RAG**: 각 씬에 필요한 청크만 검색하여 컨텍스트 8K 이내 유지
- **Milvus 벡터 검색**: 의미 기반 청크 검색
- **한국어 출력 강제**: 프롬프트 엔지니어링 + 검증 로직
- **출처 추적(Source Refs)**: 각 씬이 어느 청크에서 생성되었는지 추적

### 1.3 주요 파일
| 파일 | 역할 |
|------|------|
| `app/services/scene_based_script_generator.py` | 씬 단위 RAG 스크립트 생성기 |
| `app/services/source_set_orchestrator.py` | 전체 파이프라인 오케스트레이션 |
| `app/models/source_set.py` | 데이터 모델 정의 |
| `app/clients/milvus_client.py` | Milvus 벡터 검색 클라이언트 |
| `app/clients/llm_client.py` | LLM API 클라이언트 |

---

## 2. 전체 플로우 다이어그램

### 2.1 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Spring Backend                                  │
│  ┌─────────────┐                                      ┌─────────────────┐   │
│  │ 교육 관리자  │ ─────────────────────────────────────▶│ 스크립트 저장    │   │
│  └─────────────┘                                      └─────────────────┘   │
└────────┬───────────────────────────────────────────────────────▲────────────┘
         │ POST /source-sets/{id}/start                          │ 콜백
         ▼                                                       │
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FastAPI (AI 서비스)                             │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                     SourceSetOrchestrator                            │   │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐   │   │
│  │  │ 1. 문서조회  │───▶│ 2. 청크조회  │───▶│ 3. 스크립트 생성         │   │   │
│  │  └─────────────┘    └─────────────┘    └─────────────────────────┘   │   │
│  │         │                  │                        │                │   │
│  │         ▼                  ▼                        ▼                │   │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐   │   │
│  │  │ Backend API │    │   Milvus    │    │ SceneBasedScriptGenerator│   │   │
│  │  └─────────────┘    └─────────────┘    └─────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                  SceneBasedScriptGenerator                           │   │
│  │                                                                       │   │
│  │   ┌────────────────┐                                                  │   │
│  │   │ 1단계: 아웃라인 │  ← 1회 LLM 호출                                  │   │
│  │   │   생성         │    (문서 메타 + 샘플 청크)                        │   │
│  │   └───────┬────────┘                                                  │   │
│  │           ▼                                                           │   │
│  │   ┌────────────────┐                                                  │   │
│  │   │ 2단계: 씬별    │  ← N회 LLM 호출                                  │   │
│  │   │ RAG + 생성    │    (씬당 Top-3 청크 검색 후 생성)                  │   │
│  │   └───────┬────────┘                                                  │   │
│  │           ▼                                                           │   │
│  │   ┌────────────────┐                                                  │   │
│  │   │ 3단계: 병합    │  ← 씬들을 챕터로 조립                             │   │
│  │   └────────────────┘                                                  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
         │                           │
         ▼                           ▼
┌─────────────────┐         ┌─────────────────┐
│     Milvus      │         │    vLLM 서버    │
│  (벡터 데이터베이스)│         │ (LLM 추론 서버)  │
└─────────────────┘         └─────────────────┘
```

### 2.2 스크립트 생성 상세 플로우

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         스크립트 생성 파이프라인                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ 입력: document_chunks                                                │   │
│  │ {                                                                    │   │
│  │   "doc-001": [                                                       │   │
│  │     {"chunk_index": 0, "chunk_text": "보안사고 발생..."},             │   │
│  │     {"chunk_index": 1, "chunk_text": "사내 챗봇 도입..."},            │   │
│  │     ...23개 청크 (6,241자)                                           │   │
│  │   ]                                                                  │   │
│  │ }                                                                    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                    │                                         │
│                                    ▼                                         │
│  ╔══════════════════════════════════════════════════════════════════════╗   │
│  ║ 1단계: 아웃라인 생성 (1회 LLM)                                        ║   │
│  ╠══════════════════════════════════════════════════════════════════════╣   │
│  ║ 입력:                                                                 ║   │
│  ║ - 문서 제목: ["사내 보안형 AI 챗봇 사용 안내"]                         ║   │
│  ║ - 샘플 콘텐츠: 처음 3개 청크 (각 300자) = 900자                       ║   │
│  ║                                                                       ║   │
│  ║ 프롬프트 크기: ~1,500 토큰                                            ║   │
│  ║ max_tokens: 1,500                                                     ║   │
│  ║ temperature: 0.3                                                      ║   │
│  ╚══════════════════════════════════════════════════════════════════════╝   │
│                                    │                                         │
│                                    ▼                                         │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ 출력: ScriptOutline                                                  │   │
│  │ {                                                                    │   │
│  │   "title": "사내 보안형 AI 챗봇 사용 안내",                           │   │
│  │   "chapters": [                                                      │   │
│  │     {                                                                │   │
│  │       "chapter_index": 0,                                            │   │
│  │       "title": "보안사고 예방의 중요성",                              │   │
│  │       "scenes": [                                                    │   │
│  │         {                                                            │   │
│  │           "scene_index": 0,                                          │   │
│  │           "title": "보안사고 발생 배경",                              │   │
│  │           "purpose": "도입",                                         │   │
│  │           "keywords": ["보안사고", "정보유출", "예방"],               │   │
│  │           "target_duration_sec": 30                                  │   │
│  │         }                                                            │   │
│  │       ]                                                              │   │
│  │     },                                                               │   │
│  │     ...2~3개 챕터, 총 4~8개 씬                                       │   │
│  │   ],                                                                 │   │
│  │   "total_scenes": 5                                                  │   │
│  │ }                                                                    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                    │                                         │
│                                    ▼                                         │
│  ╔══════════════════════════════════════════════════════════════════════╗   │
│  ║ 2단계: 씬별 RAG 검색 + 생성 (N회 LLM)                                 ║   │
│  ╠══════════════════════════════════════════════════════════════════════╣   │
│  ║                                                                       ║   │
│  ║   ┌─────────────────────────────────────────────────────────────┐    ║   │
│  ║   │ 씬 1: "보안사고 발생 배경"                                   │    ║   │
│  ║   ├─────────────────────────────────────────────────────────────┤    ║   │
│  ║   │ 2-1. RAG 검색                                               │    ║   │
│  ║   │   쿼리: "보안사고 발생 배경 보안사고 정보유출 예방"          │    ║   │
│  ║   │   Milvus → Top-3 청크 반환                                  │    ║   │
│  ║   │   결과: [청크2, 청크5, 청크1] (각 ~500자, 총 ~1,500자)       │    ║   │
│  ║   ├─────────────────────────────────────────────────────────────┤    ║   │
│  ║   │ 2-2. 씬 스크립트 생성                                       │    ║   │
│  ║   │   프롬프트: 시스템 + 씬정보 + 근거자료(1,500자)             │    ║   │
│  ║   │   프롬프트 크기: ~2,500 토큰                                │    ║   │
│  ║   │   max_tokens: 800                                           │    ║   │
│  ║   │   temperature: 0.4                                          │    ║   │
│  ║   └─────────────────────────────────────────────────────────────┘    ║   │
│  ║                              │                                        ║   │
│  ║                              ▼                                        ║   │
│  ║   ┌─────────────────────────────────────────────────────────────┐    ║   │
│  ║   │ 출력: GeneratedScene                                        │    ║   │
│  ║   │ {                                                           │    ║   │
│  ║   │   "narration": "안녕하세요, 여러분. 오늘은 사내 보안의...", │    ║   │
│  ║   │   "caption": "보안사고 예방",                               │    ║   │
│  ║   │   "visual_type": "KEY_POINTS",                              │    ║   │
│  ║   │   "visual_text": "1. 보안사고 예방\n2. 정보유출 방지...",   │    ║   │
│  ║   │   "highlight_terms": ["보안사고", "정보유출"],              │    ║   │
│  ║   │   "visual_description": "핵심 포인트 3가지가 순차적으로...",│    ║   │
│  ║   │   "transition": "fade",                                     │    ║   │
│  ║   │   "duration_sec": 30,                                       │    ║   │
│  ║   │   "source_refs": [                                          │    ║   │
│  ║   │     {"document_id": "doc-001", "chunk_index": 2},           │    ║   │
│  ║   │     {"document_id": "doc-001", "chunk_index": 5},           │    ║   │
│  ║   │     {"document_id": "doc-001", "chunk_index": 1}            │    ║   │
│  ║   │   ]                                                         │    ║   │
│  ║   │ }                                                           │    ║   │
│  ║   └─────────────────────────────────────────────────────────────┘    ║   │
│  ║                                                                       ║   │
│  ║   [씬 2, 씬 3, ...씬 N 동일 과정 반복]                               ║   │
│  ║                                                                       ║   │
│  ╚══════════════════════════════════════════════════════════════════════╝   │
│                                    │                                         │
│                                    ▼                                         │
│  ╔══════════════════════════════════════════════════════════════════════╗   │
│  ║ 3단계: 병합                                                           ║   │
│  ╠══════════════════════════════════════════════════════════════════════╣   │
│  ║ - 씬들을 챕터 단위로 그룹화                                           ║   │
│  ║ - 챕터별 duration_sec 합산                                            ║   │
│  ║ - 전체 total_duration_sec 계산                                        ║   │
│  ╚══════════════════════════════════════════════════════════════════════╝   │
│                                    │                                         │
│                                    ▼                                         │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ 최종 출력: GeneratedScript                                           │   │
│  │ {                                                                    │   │
│  │   "script_id": "script-4cf2b5d956fc",                               │   │
│  │   "title": "사내 보안형 AI 챗봇 사용 안내",                          │   │
│  │   "total_duration_sec": 100.0,                                       │   │
│  │   "llm_model": "meta-llama/Meta-Llama-3-8B-Instruct",               │   │
│  │   "chapters": [                                                      │   │
│  │     {                                                                │   │
│  │       "chapter_index": 0,                                            │   │
│  │       "title": "보안사고 예방의 중요성",                             │   │
│  │       "duration_sec": 30.0,                                          │   │
│  │       "scenes": [GeneratedScene, ...]                                │   │
│  │     },                                                               │   │
│  │     ...                                                              │   │
│  │   ]                                                                  │   │
│  │ }                                                                    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 단계별 상세 구현

### 3.1 1단계: 아웃라인 생성

**파일**: `scene_based_script_generator.py` - `_generate_outline()` (라인 363-485)

#### 입력
```python
doc_titles: List[str]  # 문서 제목 리스트
all_chunks: List[Dict[str, Any]]  # 전체 청크 (샘플 추출용)
```

#### 처리 로직
1. 문서 제목 목록 구성
2. 처음 3개 청크에서 각 300자씩 샘플 추출 (총 900자)
3. LLM에 아웃라인 생성 요청

#### 프롬프트 구성
```
시스템 프롬프트:
- 역할: 법정의무교육 영상 스크립트 기획 전문가
- 한국어 출력 강제
- JSON 스키마 정의

사용자 프롬프트:
- 문서 목록
- 문서 내용 샘플 (800자)
```

#### 출력
```python
@dataclass
class ScriptOutline:
    title: str                          # 스크립트 제목
    chapters: List[ChapterOutline]      # 챕터 목록
    total_scenes: int                   # 총 씬 수

@dataclass
class ChapterOutline:
    chapter_index: int                  # 챕터 순서
    title: str                          # 챕터 제목
    scenes: List[SceneOutline]          # 씬 목록

@dataclass
class SceneOutline:
    scene_index: int                    # 씬 순서
    title: str                          # 씬 제목
    purpose: str                        # 목적 (도입/설명/사례/정리)
    keywords: List[str]                 # RAG 검색용 키워드
    target_duration_sec: float          # 목표 길이 (초)
```

#### LLM 설정
| 파라미터 | 값 |
|---------|-----|
| model | meta-llama/Meta-Llama-3-8B-Instruct |
| temperature | 0.3 |
| max_tokens | 1,500 |

---

### 3.2 2단계: 씬별 RAG 검색 + 생성

**파일**: `scene_based_script_generator.py` - `_generate_scenes_with_rag_metrics()` (라인 551-641)

#### 2단계-1: RAG 검색

**메서드**: `_search_chunks_for_scene()` (라인 667-711)

```python
# 검색 쿼리 구성
query = f"{scene.title} {' '.join(scene.keywords)}"
# 예: "보안사고 발생 배경 보안사고 정보유출 예방"

# Milvus 벡터 검색
results = await self._milvus_client.search(
    query=query,
    top_k=3,  # 씬당 3개 청크
)
```

**폴백**: Milvus 검색 실패 시 키워드 기반 텍스트 매칭

```python
def _keyword_search_fallback(self, keywords, all_chunks):
    scored_chunks = []
    for chunk in all_chunks:
        text = chunk.get("text", "").lower()
        score = sum(1 for kw in keywords if kw.lower() in text)
        if score > 0:
            scored_chunks.append((score, chunk))
    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    return [chunk for _, chunk in scored_chunks[:self._top_k]]
```

#### 2단계-2: 씬 스크립트 생성

**메서드**: `_generate_single_scene()` (라인 732-888)

**프롬프트 구성**:
```
시스템 프롬프트:
- 역할: 법정의무교육 영상 스크립트 작성 전문가
- 한국어 출력 강제 (영어 시작 금지)
- JSON 스키마 (한국어 예시 포함)
- visual_type 선택 가이드
- 한국어 강제 지침 (KOREAN_ENFORCEMENT)

사용자 프롬프트:
- 씬 제목, 목적, 목표 길이
- 근거 자료 (Top-K 청크, 각 500자 제한)
```

#### LLM 설정
| 파라미터 | 값 |
|---------|-----|
| model | meta-llama/Meta-Llama-3-8B-Instruct |
| temperature | 0.4 (재시도 시 0.2) |
| max_tokens | 800 |

#### 한국어 검증 로직

```python
# 한국어 비율 검사 (최소 30%)
def _is_korean_output(text: str, min_ratio: float = 0.3) -> bool:
    korean_chars = len(re.findall(r'[\u3131-\u3163\uac00-\ud7a3]', text))
    total_chars = len(re.sub(r'\s', '', text))
    return (korean_chars / total_chars) >= min_ratio

# 영어 시작 문구 검사
def _has_english_start(text: str) -> bool:
    english_starts = ["I'd", "According to", "Based on", "Sure", ...]
    return any(text.strip().lower().startswith(e.lower()) for e in english_starts)
```

**재시도 로직**:
1. 첫 시도 (temperature=0.4)
2. 한국어 검증 실패 시 재시도 (temperature=0.2)
3. 최대 2회 시도 후 실패 처리

---

### 3.3 3단계: 병합

**메서드**: `generate_script()` 내 병합 로직 (라인 317-330)

```python
# 챕터별 duration 합산
total_duration = sum(ch.duration_sec for ch in chapters)

# GeneratedScript 조립
script = GeneratedScript(
    script_id=script_id,
    education_id=education_id,
    source_set_id=source_set_id,
    title=outline.title,
    total_duration_sec=total_duration,
    version=1,
    llm_model=self._model,
    chapters=chapters,
)
```

---

## 4. 데이터 모델

### 4.1 GeneratedScene (씬)

**파일**: `app/models/source_set.py` (라인 234-302)

```python
class GeneratedScene(BaseModel):
    # 필수 필드
    scene_index: int           # 씬 순서 (0-based)
    purpose: str               # 씬 목적 (도입/설명/사례/정리)
    narration: str             # TTS 나레이션 텍스트 (150-250자)
    duration_sec: float        # 씬 길이 (초)

    # 시각 자료 필드 (영상 렌더링용)
    caption: Optional[str]                # 화면 하단 자막 (30자 이내)
    visual_type: Optional[str]            # 시각 자료 유형
    visual_text: Optional[str]            # 화면 표시 텍스트
    visual_description: Optional[str]     # 편집자용 설명
    highlight_terms: List[str]            # 강조 용어 (3-5개)
    transition: Optional[str]             # 화면 전환 효과

    # 메타 필드
    confidence_score: Optional[float]     # 신뢰도 점수 (0-1)
    source_refs: List[SourceRef]          # 출처 참조
```

### 4.2 visual_type 종류

| 타입 | 용도 | 매핑되는 씬 목적 |
|------|------|-----------------|
| `TITLE_SLIDE` | 제목/타이틀 슬라이드 | 도입 |
| `KEY_POINTS` | 핵심 포인트 목록 | 설명 |
| `COMPARISON` | 비교 테이블 | 비교 |
| `DIAGRAM` | 다이어그램/흐름도 | 설명 |
| `EXAMPLE` | 사례 카드 | 사례 |
| `WARNING` | 경고/주의 화면 | 주의 |
| `SUMMARY` | 요약 슬라이드 | 정리 |

### 4.3 GeneratedChapter (챕터)

```python
class GeneratedChapter(BaseModel):
    chapter_index: int         # 챕터 순서 (0-based)
    title: str                 # 챕터 제목
    duration_sec: float        # 챕터 총 길이 (초)
    scenes: List[GeneratedScene]  # 씬 목록
```

### 4.4 GeneratedScript (스크립트)

```python
class GeneratedScript(BaseModel):
    script_id: str             # 스크립트 ID (UUID)
    education_id: Optional[str]  # 교육 ID
    source_set_id: str         # 소스셋 ID
    title: str                 # 스크립트 제목
    total_duration_sec: float  # 전체 길이 (초)
    version: int               # 버전 (기본 1)
    llm_model: Optional[str]   # 사용된 LLM 모델
    chapters: List[GeneratedChapter]  # 챕터 목록
```

---

## 5. 데이터 흐름 및 변환

### 5.1 단계별 데이터 크기 변화

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          데이터 흐름 상세                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  [입력: Milvus 문서 청크]                                                │
│  ├── 문서: 1개 ("사내 보안형 AI 챗봇 사용 안내.docx")                    │
│  ├── 청크: 23개                                                          │
│  └── 총 텍스트: 6,241자                                                  │
│                                                                          │
│                              ▼                                           │
│                                                                          │
│  [1단계: 아웃라인 생성]                                                  │
│  ├── 입력 크기                                                           │
│  │   ├── 문서 제목: ~30자                                                │
│  │   ├── 샘플 청크: 3개 × 300자 = 900자                                  │
│  │   └── 총 입력: ~1,000자 (~500 토큰)                                   │
│  ├── 프롬프트 총 크기: ~3,000 토큰                                       │
│  │   ├── 시스템 프롬프트: ~2,000 토큰                                    │
│  │   └── 사용자 프롬프트: ~1,000 토큰                                    │
│  └── 출력                                                                │
│      ├── ScriptOutline JSON: ~800자                                      │
│      ├── 챕터: 2-3개                                                     │
│      ├── 씬: 4-8개                                                       │
│      └── 씬당 키워드: 2-3개                                              │
│                                                                          │
│                              ▼                                           │
│                                                                          │
│  [2단계: 씬별 RAG + 생성] (씬 5개 가정)                                  │
│  ├── 씬 1                                                                │
│  │   ├── RAG 검색                                                        │
│  │   │   ├── 쿼리: ~50자                                                 │
│  │   │   ├── 검색 결과: Top-3 청크                                       │
│  │   │   └── 청크 텍스트: 3 × 500자 = 1,500자                           │
│  │   ├── LLM 입력                                                        │
│  │   │   ├── 시스템 프롬프트: ~2,500 토큰                                │
│  │   │   ├── 씬 정보: ~100 토큰                                          │
│  │   │   ├── 근거 자료: ~800 토큰                                        │
│  │   │   └── 총 입력: ~3,400 토큰                                        │
│  │   └── 출력: GeneratedScene JSON (~600자)                              │
│  │       ├── narration: 150-250자                                        │
│  │       ├── caption: ~30자                                              │
│  │       ├── visual_text: ~100자                                         │
│  │       ├── visual_description: ~50자                                   │
│  │       ├── highlight_terms: 3-5개                                      │
│  │       └── source_refs: 3개                                            │
│  ├── 씬 2: 동일 구조                                                     │
│  ├── 씬 3: 동일 구조                                                     │
│  ├── 씬 4: 동일 구조                                                     │
│  └── 씬 5: 동일 구조                                                     │
│                                                                          │
│  [2단계 총계]                                                            │
│  ├── LLM 호출: 5회                                                       │
│  ├── 총 입력 토큰: ~17,000 토큰                                          │
│  ├── 총 출력 토큰: ~2,000 토큰                                           │
│  └── 생성된 씬: 5개                                                      │
│                                                                          │
│                              ▼                                           │
│                                                                          │
│  [3단계: 병합]                                                           │
│  └── 출력: GeneratedScript                                               │
│      ├── script_id: 18자                                                 │
│      ├── title: ~30자                                                    │
│      ├── total_duration_sec: 100초                                       │
│      ├── chapters: 2개                                                   │
│      │   ├── 챕터 0: 30초, 1씬                                           │
│      │   └── 챕터 1: 70초, 4씬                                           │
│      └── 총 JSON 크기: ~4,000자                                          │
│                                                                          │
│                              ▼                                           │
│                                                                          │
│  [최종 출력: 백엔드 콜백]                                                │
│  └── SourceSetCompleteRequest                                            │
│      ├── video_id: 문자열                                                │
│      ├── status: "COMPLETED"                                             │
│      ├── source_set_status: "SCRIPT_READY"                               │
│      ├── script: GeneratedScript (위 구조)                               │
│      └── documents: [{document_id, status, fail_reason}]                 │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 토큰 사용량 분석

| 단계 | LLM 호출 | 입력 토큰 | 출력 토큰 | 합계 |
|------|---------|----------|----------|------|
| 1단계: 아웃라인 | 1회 | ~3,000 | ~500 | ~3,500 |
| 2단계: 씬 생성 (N개) | N회 | N × 3,400 | N × 400 | N × 3,800 |
| **총계 (씬 5개)** | **6회** | **~20,000** | **~2,500** | **~22,500** |

### 5.3 컨텍스트 제한 우회 효과

| 방식 | 문서 크기 | 필요 토큰 | 8K 제한 |
|------|----------|----------|---------|
| **기존 (전체 문서)** | 6,241자 | ~4,000 토큰 | ❌ 초과 위험 |
| **씬 단위 RAG** | 1,500자/씬 | ~1,000 토큰/씬 | ✅ 제한 내 |

---

## 6. API 스펙

### 6.1 스크립트 생성 시작

**POST** `/internal/ai/source-sets/{sourceSetId}/start`

**Request Body**:
```json
{
  "videoId": "video-001",
  "educationId": "edu-001",
  "requestId": "req-xxx",
  "traceId": "trace-xxx",
  "llmModelHint": "meta-llama/Meta-Llama-3-8B-Instruct"
}
```

**Response (202 Accepted)**:
```json
{
  "received": true,
  "sourceSetId": "source-set-001",
  "status": "LOCKED"
}
```

### 6.2 백엔드 콜백 (스크립트 완료)

**POST** `/internal/callbacks/source-sets/{sourceSetId}/complete`

**Request Body (성공)**:
```json
{
  "videoId": "video-001",
  "status": "COMPLETED",
  "sourceSetStatus": "SCRIPT_READY",
  "script": {
    "scriptId": "script-4cf2b5d956fc",
    "title": "사내 보안형 AI 챗봇 사용 안내",
    "totalDurationSec": 100.0,
    "llmModel": "meta-llama/Meta-Llama-3-8B-Instruct",
    "chapters": [
      {
        "chapterIndex": 0,
        "title": "보안사고 예방의 중요성",
        "durationSec": 30.0,
        "scenes": [
          {
            "sceneIndex": 0,
            "purpose": "도입",
            "narration": "안녕하세요, 여러분...",
            "caption": "보안사고 예방",
            "visualType": "KEY_POINTS",
            "visualText": "1. 보안사고 예방\n2. 정보유출 방지",
            "visualDescription": "핵심 포인트 3가지가 순차적으로...",
            "highlightTerms": ["보안사고", "정보유출"],
            "transition": "fade",
            "durationSec": 30.0,
            "confidenceScore": 0.8,
            "sourceRefs": [
              {"documentId": "doc-001", "chunkIndex": 2},
              {"documentId": "doc-001", "chunkIndex": 5}
            ]
          }
        ]
      }
    ]
  },
  "documents": [
    {"documentId": "doc-001", "status": "COMPLETED", "failReason": null}
  ],
  "requestId": "req-xxx",
  "traceId": "trace-xxx"
}
```

---

## 7. 에러 처리 및 폴백

### 7.1 실패 원인 코드 (FailReason)

| 코드 | 설명 | 처리 |
|------|------|------|
| `OUTLINE_PARSE_ERROR` | 아웃라인 JSON 파싱 실패 | 폴백 스크립트 |
| `OUTLINE_EMPTY` | 아웃라인 생성 결과 없음 | 폴백 스크립트 |
| `RETRIEVE_EMPTY` | RAG 검색 결과 없음 | 키워드 폴백 검색 |
| `SCENE_PARSE_ERROR` | 씬 JSON 파싱 실패 | 해당 씬 스킵 |
| `NON_KOREAN_OUTPUT` | 한국어 검증 실패 | 재시도 후 스킵 |
| `LLM_ERROR` | LLM API 호출 실패 | 재시도 후 폴백 |

### 7.2 폴백 스크립트

모든 생성 실패 시 최소한의 폴백 스크립트 반환:

```python
def _generate_fallback_script(self, ...):
    return GeneratedScript(
        script_id=script_id,
        title=f"{doc_titles[0]} (자동 생성 실패 - 폴백)",
        total_duration_sec=15.0,
        llm_model="fallback",
        chapters=[
            GeneratedChapter(
                chapter_index=0,
                title="교육 내용",
                duration_sec=15.0,
                scenes=[
                    GeneratedScene(
                        scene_index=0,
                        purpose="도입",
                        narration=f"{title}에 대한 교육을 시작합니다.",
                        caption="교육 시작",
                        visual="타이틀 슬라이드",
                        duration_sec=15.0,
                        confidence_score=0.3,
                    )
                ]
            )
        ]
    )
```

---

## 8. 성능 메트릭

### 8.1 GenerationMetrics

```python
@dataclass
class GenerationMetrics:
    outline_ms: float = 0.0           # 아웃라인 생성 시간
    total_retrieve_ms: float = 0.0    # 전체 RAG 검색 시간
    total_scene_llm_ms: float = 0.0   # 전체 씬 LLM 시간
    total_ms: float = 0.0             # 전체 생성 시간
    scene_count: int = 0              # 생성된 씬 수
    failed_scene_count: int = 0       # 실패한 씬 수
    korean_validation_pass: int = 0   # 한국어 검증 통과 수
    korean_validation_fail: int = 0   # 한국어 검증 실패 수
    retry_count: int = 0              # 재시도 횟수
    fail_reasons: List[str]           # 실패 원인 목록
```

### 8.2 실제 테스트 결과

| 항목 | 값 |
|------|-----|
| 문서 | 1개 (23 청크, 6,241자) |
| 생성 시간 | 20-25초 |
| 챕터 수 | 2개 |
| 씬 수 | 3-5개 |
| 총 영상 길이 | 100초 (1.7분) |
| LLM 호출 | 4-6회 |

### 8.3 로그 출력 예시

```
Script generation completed: script_id=script-4cf2b5d956fc,
chapters=2, duration=100s |
METRICS: outline_ms=3500, retrieve_ms=1200, scene_llm_ms=18000,
total_ms=23000 |
scenes=5, failed=0, korean_pass=5, korean_fail=0, retries=0
```

---

## 9. 설정 값

### 9.1 LLM 설정 (SceneBasedScriptGenerator)

```python
DEFAULT_MODEL = "meta-llama/Meta-Llama-3-8B-Instruct"
MAX_TOKENS_OUTLINE = 1500   # 아웃라인 생성용
MAX_TOKENS_SCENE = 800      # 씬 생성용
MAX_TOKENS_POLISH = 1000    # 다듬기용 (미사용)
DEFAULT_TOP_K = 3           # 씬당 검색 청크 수
```

### 9.2 한국어 검증 설정

```python
MIN_KOREAN_RATIO = 0.3      # 최소 한국어 비율 (30%)
MAX_KOREAN_RETRY = 1        # 한국어 검증 실패 시 재시도 횟수
RETRY_TEMPERATURE = 0.2     # 재시도 시 temperature
```

---

## 10. 향후 개선 사항

1. **한국어 모델 적용**: `LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct` 또는 `MLP-KTLim/llama-3-Korean-Bllossom-8B`
2. **병렬 씬 생성**: asyncio.gather로 씬 생성 병렬화
3. **3단계 일관성 다듬기**: 톤/문체 통일 LLM 호출 추가
4. **캐싱**: 동일 문서에 대한 아웃라인 캐싱

---

## 11. 부록: 실제 생성 결과 예시

```json
{
  "script_id": "script-4cf2b5d956fc",
  "title": "사내 보안형 AI 챗봇 사용 안내",
  "total_duration_sec": 100.0,
  "chapters": [
    {
      "chapter_index": 0,
      "title": "보안사고 예방의 중요성",
      "duration_sec": 30.0,
      "scenes": [
        {
          "scene_index": 0,
          "purpose": "도입",
          "narration": "안녕하세요, 여러분. 오늘은 사내 보안의 중요성에 대해 알아보겠습니다. 최근 우리 회사는 보안 사고·정보 유출 사고가 발생했습니다. 이러한 사고는 잘못된 채널로 데이터를 전달하거나, 공용 환경에서 화면이 노출되거나, 허용되지 않은 장비·서비스에 데이터를 저장하는 등의 사고가 발생할 수 있습니다.",
          "caption": "보안사고 예방",
          "visual_type": "KEY_POINTS",
          "visual_text": "1. 잘못된 채널로 데이터 전달\n2. 공용 환경에서 화면 노출\n3. 허용되지 않은 장비·서비스에 데이터 저장",
          "visual_description": "핵심 포인트 3가지가 순차적으로 나타나는 애니메이션",
          "highlight_terms": ["보안사고", "정보유출", "사고 예방"],
          "transition": "fade",
          "duration_sec": 30.0,
          "confidence_score": 0.8,
          "source_refs": [
            {"document_id": "인사팀_추가교육자료_재택·유연근무 제도 운영 실무 가이드.docx", "chunk_index": 30},
            {"document_id": "(1교시)법령위반_사례과정_개인정보_보호법_위반사례.pdf", "chunk_index": 2}
          ]
        }
      ]
    },
    {
      "chapter_index": 1,
      "title": "사내 보안형 AI 챗봇 도입 목적",
      "duration_sec": 70.0,
      "scenes": [
        {
          "scene_index": 0,
          "purpose": "설명",
          "narration": "안녕하세요, 여러분. 오늘은 사내 보안형 AI 챗봇의 도입 목적에 대해 알아보겠습니다. 최근 우리 회사는 외부 AI 서비스 사용으로 인한 정보 유출 사고가 발생했습니다. 이를 방지하기 위해 우리는 사내 보안형 AI 챗봇을 도입했습니다.",
          "caption": "사내 챗봇의 도입 목적",
          "visual_type": "KEY_POINTS",
          "visual_text": "1. 재발 방지\n2. 보안과 효율의 균형\n3. 직무교육·사규·업무자료 활용성 강화",
          "visual_description": "핵심 포인트 3가지가 순차적으로 나타나는 애니메이션",
          "highlight_terms": ["보안사고", "정보유출", "AI 사용"],
          "transition": "fade",
          "duration_sec": 40.0,
          "confidence_score": 0.8,
          "source_refs": [
            {"document_id": "전체공통_추가교육자료_사내 보안형 AI 챗봇 사용 안내.docx", "chunk_index": 3}
          ]
        }
      ]
    }
  ]
}
```

---

**문서 버전**: 1.0
**작성일**: 2026-01-02
**작성자**: AI 서비스 개발팀
