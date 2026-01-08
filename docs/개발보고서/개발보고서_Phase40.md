# Phase 40: Scene Audio/Caption Timeline Enhancement

## 개요

"슬라이드 낭독" 느낌을 줄이기 위해 씬(Scene) 단위 렌더의 품질과 정합성을 향상시키는 Phase입니다.

### 핵심 목표

1. **문장 단위 TTS 합성 파이프라인** - 나레이션을 문장으로 분할하여 개별 TTS 생성 후 합성
2. **오디오 길이 기반 씬 duration 확정** - 스크립트 duration 대신 실제 오디오 길이 사용
3. **캡션 타임라인(JSON/SRT) 생성** - 문장별 정확한 시작/종료 시간 제공

---

## 변경 파일 리스트

### 신규 파일

| 파일 | 설명 |
|------|------|
| `app/utils/text_splitter.py` | 문장 분할 유틸리티 (TTS용) |
| `app/services/scene_audio_service.py` | 씬 오디오 서비스 (TTS concat + 캡션) |
| `tests/test_phase40_scene_audio.py` | Phase 40 테스트 (26개) |
| `docs/DEVELOPMENT_REPORT_PHASE40.md` | 개발 보고서 |

### 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/core/config.py` | `SCENE_SILENCE_PADDING_SEC`, `TTS_MAX_SENTENCE_LENGTH` 설정 추가 |
| `app/services/video_renderer_mvp.py` | SceneAudioService 통합, 캡션 기반 자막 생성 |

---

## 구현 상세

### 1. 문장 분할 유틸 (`split_sentences`)

**위치:** `app/utils/text_splitter.py`

**분할 규칙:**
1. `\n` 기준 1차 분할 후 trim
2. `. ? ! …` 및 한국어 종결 표현(`다.` `요.` `죠.` 등) 뒤에서 2차 분할
3. 빈 문장 제거
4. 300자 이상 긴 문장은 쉼표/공백 기준으로 추가 분할

```python
from app.utils.text_splitter import split_sentences

sentences = split_sentences("첫 번째 문장입니다. 두 번째 문장이에요!")
# ["첫 번째 문장입니다.", "두 번째 문장이에요!"]
```

### 2. 문장별 TTS 생성 + Concat

**위치:** `app/services/scene_audio_service.py`

**파이프라인:**
1. `split_sentences(narration)` → 문장 리스트
2. 각 문장에 TTS 합성 → 개별 오디오 파일
3. FFmpeg `concat` demuxer로 하나의 씬 오디오로 합성
4. 실패한 문장은 **무음(0.5초)으로 대체** (Job 전체 실패 금지)

```python
service = SceneAudioService()
result = await service.generate_scene_audio(
    scene_id="scene-001",
    narration="문장1. 문장2. 문장3.",
    output_dir=Path("./output"),
)
# result.audio_path = "./output/scene-001_audio.mp3"
# result.sentence_count = 3
```

### 3. 오디오 길이 기반 Duration + 패딩

**공식:**
```
scene_duration_sec = audio_duration_sec + SCENE_SILENCE_PADDING_SEC
```

**환경변수:**
- `SCENE_SILENCE_PADDING_SEC`: 기본 0.5초 (씬 끝 여백)
- `TTS_MAX_SENTENCE_LENGTH`: 기본 300자 (분할 임계값)

### 4. 캡션 타임라인 생성

**JSON 스키마:**
```json
[
  {"start": 0.00, "end": 1.23, "text": "문장1"},
  {"start": 1.23, "end": 2.80, "text": "문장2"}
]
```

**SRT 생성:**
```python
from app.services.scene_audio_service import generate_srt

srt_content = generate_srt(result.captions)
# 1
# 00:00:00,000 --> 00:00:01,230
# 문장1
#
# 2
# 00:00:01,230 --> 00:00:02,800
# 문장2
```

---

## 샘플 동작 로그

```
INFO - Scene scene-001: 3 sentences to process
DEBUG - TTS generated: scene=scene-001, sent=0, duration=1.20s
DEBUG - TTS generated: scene=scene-001, sent=1, duration=1.35s
DEBUG - TTS generated: scene=scene-001, sent=2, duration=1.10s
INFO - Scene scene-001 audio generated: duration=4.15s, captions=3, failed=0/3
```

**결과 JSON:**
```json
{
  "scene_id": "scene-001",
  "audio_path": "./output/scene-001_audio.mp3",
  "duration_sec": 4.15,
  "audio_duration_sec": 3.65,
  "captions": [
    {"start": 0.0, "end": 1.2, "text": "첫 번째 문장입니다."},
    {"start": 1.2, "end": 2.55, "text": "두 번째 문장이에요."},
    {"start": 2.55, "end": 3.65, "text": "세 번째 문장이죠."}
  ],
  "sentence_count": 3,
  "failed_sentences": 0
}
```

---

## 테스트 결과

```
tests/test_phase40_scene_audio.py - 26 passed

TestSplitSentences (10 tests)
- 빈 문자열, 개행, 긴 문장, 한국어 종결어미 처리

TestSceneAudioService (6 tests)
- 3문장 → 3오디오 → concat 결과 파일 생성
- duration = audio_duration + padding 규칙
- 캡션 start/end 누적 (end >= start)
- TTS 실패 시 무음 대체

TestCaptionEntry (3 tests)
- to_dict(), SRT 생성

TestSceneAudioResult (2 tests)
- to_dict(), get_captions_json()

TestMockProviderIntegration (1 test)
- Mock provider 통합

TestEdgeCases (4 tests)
- 공백만, 구두점만, 짧은 문장, 유니코드
```

---

## 설정 옵션

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `SCENE_SILENCE_PADDING_SEC` | 0.5 | 씬 끝 무음 패딩 (초) |
| `TTS_MAX_SENTENCE_LENGTH` | 300 | 문장 최대 길이 (초과 시 분할) |

---

## 기존 API 영향

- **API 스펙 변경 없음** - 내부 구현만 변경
- **기존 video_renderer_mvp.py 통합** - SceneAudioService 주입으로 확장
- **Phase 32~37과 충돌 없음** - 기존 TTS provider 재사용

---

## 향후 개선 가능

1. 문장별 감정/속도 조절 (prosody control)
2. 다국어 문장 분할 패턴 확장
3. 오디오 품질 향상 (44.1kHz stereo 통일)
4. 캡션 스타일링 옵션 (위치, 색상 등)
