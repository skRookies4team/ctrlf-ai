# Phase 37: "영상처럼 보이게" 최소 연출

**작성일**: 2025-12-19
**Phase**: 37
**상태**: 완료
**테스트 결과**: 34 passed

---

## 1. 개요

Phase 37에서는 렌더링된 영상이 "영상처럼 보이게" 하기 위한 최소한의 연출 기능을 구현했습니다.

### 1.1 목표

- **씬 이미지 생성**: 각 씬에 대해 title/body/highlight가 포함된 PNG 이미지 생성
- **Ken Burns 효과**: 정적 이미지에 zoompan 애니메이션 적용 (줌 인/아웃 교대)
- **Fade 전환**: 씬 간 부드러운 페이드 전환
- **키워드 하이라이트**: 따옴표, 약어, 숫자+단위 자동 강조

### 1.2 제약조건

- 백엔드 코드/엔드포인트 변경 없음
- S3 직접 업로드 없음 (기존 BackendPresignedStorageProvider 유지)
- 중간 산출물(씬 PNG)은 로컬 임시 폴더에만 저장 (업로드 안함)
- VIDEO_VISUAL_STYLE 환경변수로 모드 전환

---

## 2. 아키텍처

### 2.1 전체 흐름

```
┌─────────────────────────────────────────────────────────────────────────┐
│  VIDEO_VISUAL_STYLE = "basic" (기존)                                    │
│                                                                         │
│  SceneInfo → VideoComposer → 단색 배경 + 텍스트 오버레이 → MP4          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  VIDEO_VISUAL_STYLE = "animated" (신규)                                 │
│                                                                         │
│  SceneInfo                                                              │
│      ↓                                                                  │
│  VisualPlanExtractor (deterministic rules)                              │
│      ↓                                                                  │
│  VisualPlan (title, body, highlight_terms)                              │
│      ↓                                                                  │
│  ImageAssetService (Pillow)                                             │
│      ↓                                                                  │
│  Scene PNG (1920x1080, 그라데이션 배경, 키워드 하이라이트)              │
│      ↓                                                                  │
│  VideoComposer (FFmpeg zoompan + xfade)                                 │
│      ↓                                                                  │
│  Final MP4 (Ken Burns 효과 + Fade 전환)                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 컴포넌트 다이어그램

```
┌─────────────────┐     ┌──────────────────────┐
│  RealVideo      │────▶│  VisualPlanExtractor │
│  Renderer       │     │  (visual_plan.py)    │
└─────────────────┘     └──────────────────────┘
        │                         │
        │                         ▼
        │               ┌──────────────────────┐
        │               │  VisualPlan          │
        │               │  - title             │
        │               │  - body              │
        │               │  - highlight_terms   │
        │               └──────────────────────┘
        │                         │
        │                         ▼
        │               ┌──────────────────────┐
        │──────────────▶│  ImageAssetService   │
        │               │  (Pillow)            │
        │               └──────────────────────┘
        │                         │
        │                         ▼
        │               ┌──────────────────────┐
        │               │  Scene PNGs          │
        │               │  (로컬 임시 폴더)    │
        │               └──────────────────────┘
        │                         │
        ▼                         ▼
┌─────────────────┐     ┌──────────────────────┐
│  VideoComposer  │◀────│  scene.image_path    │
│  (FFmpeg)       │     └──────────────────────┘
└─────────────────┘
        │
        ▼
┌─────────────────┐
│  Final MP4      │
│  (zoompan+fade) │
└─────────────────┘
```

---

## 3. VisualPlan 추출 (Deterministic Rules)

### 3.1 VisualPlan 구조

```python
@dataclass
class VisualPlan:
    scene_id: int
    title: str           # 화면 상단 제목
    body: str            # 화면 본문 (요약)
    highlight_terms: List[str]  # 강조 키워드
    duration_sec: Optional[float] = None
```

### 3.2 Title 추출 우선순위

```
1. on_screen_text (있으면 사용)
2. caption (on_screen_text 없으면)
3. narration 첫 문장 (둘 다 없으면)
4. "씬 {scene_id}" (모두 없으면)
```

### 3.3 Body 추출

```
- narration에서 첫 문장 이후 내용
- 최대 2문장
- 최대 100자로 truncate
```

### 3.4 Highlight Terms 추출 (LLM 없이 deterministic)

| 패턴 | 정규식 | 예시 |
|------|--------|------|
| 따옴표 텍스트 | `["\']([^"\']{2,20})["\']` | "중요한 키워드" |
| 대문자 약어 | `(?<![A-Za-z])([A-Z]{2,10})(?![A-Za-z])` | USB, API, JSON |
| 숫자+단위 | `(\d+(?:MB\|GB\|TB\|초\|분\|원\|...))` | 16GB, 30초, 5000원 |
| 강조 패턴 | `\*\*([^*]+)\*\*\|[[^\]]+\]` | **중요**, [핵심] |

---

## 4. ImageAssetService (Pillow)

### 4.1 이미지 사양

| 항목 | 값 |
|------|-----|
| 크기 | 1920 x 1080 (FHD) |
| 포맷 | PNG |
| 배경 | 그라데이션 (dark blue-gray → lighter) |
| 제목 폰트 | 72pt, 흰색, 중앙 정렬 |
| 본문 폰트 | 36pt, 연회색, 중앙 정렬 |
| 하이라이트 | 밑줄 (light blue) |

### 4.2 그라데이션 배경

```python
background_color = (30, 30, 46)     # Dark blue-gray (상단)
gradient_end_color = (45, 45, 68)   # Slightly lighter (하단)
```

### 4.3 한글 폰트 지원

```
폰트 검색 순서:
1. VIDEO_FONT_PATH 환경변수
2. Windows: C:/Windows/Fonts/malgun.ttf (맑은 고딕)
3. Linux: /usr/share/fonts/truetype/nanum/NanumGothic.ttf
4. Mac: /System/Library/Fonts/AppleSDGothicNeo.ttc
5. 기본 폰트 fallback
```

---

## 5. VideoComposer Animated 모드

### 5.1 Ken Burns 효과 (zoompan)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  FFmpeg zoompan 필터                                                    │
│                                                                         │
│  홀수 씬: zoom=1.0 → 1.1 (줌 인)                                       │
│  짝수 씬: zoom=1.1 → 1.0 (줌 아웃)                                     │
│                                                                         │
│  공식: z='if(eq(on,1),1.0,z+0.1/{total_frames})'                       │
│        x='iw/2-(iw/zoom/2)'                                             │
│        y='ih/2-(ih/zoom/2)'                                             │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Fade 전환 (xfade)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  FFmpeg xfade 필터                                                      │
│                                                                         │
│  transition=fade                                                        │
│  duration=0.5초 (기본값)                                                │
│  offset=이전_씬_종료_시점 - fade_duration                               │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.3 FFmpeg 명령 구조

```bash
ffmpeg -y \
  -loop 1 -t 4 -i scene_000.png \
  -loop 1 -t 5 -i scene_001.png \
  -loop 1 -t 3 -i scene_002.png \
  -i audio.mp3 \
  -filter_complex "
    [0:v]zoompan=z='...':d=120:s=1920x1080:fps=30[v0];
    [1:v]zoompan=z='...':d=150:s=1920x1080:fps=30[v1];
    [2:v]zoompan=z='...':d=90:s=1920x1080:fps=30[v2];
    [v0][v1]xfade=transition=fade:duration=0.5:offset=3.5[xf0];
    [xf0][v2]xfade=transition=fade:duration=0.5:offset=8.0[outv]
  " \
  -map "[outv]" -map 3:a \
  -c:v libx264 -c:a aac \
  output.mp4
```

---

## 6. 환경변수

### 6.1 Phase 37 신규 환경변수

```env
# 시각 스타일 (basic: 단색 배경, animated: 씬 이미지+Ken Burns+fade)
VIDEO_VISUAL_STYLE=basic

# Animated 모드 설정
VIDEO_WIDTH=1920          # 영상 너비
VIDEO_HEIGHT=1080         # 영상 높이
VIDEO_FPS=30              # 프레임 레이트
VIDEO_FADE_DURATION=0.5   # 씬 전환 fade 시간 (초)
VIDEO_KENBURNS_ZOOM=1.1   # Ken Burns 줌 비율 (1.0 = 줌 없음)

# 폰트 설정 (선택)
VIDEO_FONT_PATH=/path/to/font.ttf
```

### 6.2 모드 전환

| VIDEO_VISUAL_STYLE | 설명 |
|--------------------|------|
| `basic` (기본값) | 단색 배경 + drawtext 오버레이, 기존 방식 |
| `animated` | 씬 이미지 생성 + Ken Burns + fade 전환 |

---

## 7. 파일 구조

### 7.1 신규/수정 파일

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `app/core/config.py` | 수정 | Phase 37 환경변수 추가 |
| `app/services/visual_plan.py` | 신규 | VisualPlan + Extractor |
| `app/services/image_asset_service.py` | 신규 | Pillow 이미지 생성 |
| `app/services/video_composer.py` | 수정 | animated 모드 (zoompan+xfade) |
| `app/services/video_renderer_real.py` | 수정 | _render_slides 이미지 생성 통합 |
| `tests/test_phase37_visual_render.py` | 신규 | Phase 37 테스트 (34개) |

### 7.2 중간 산출물 (로컬만)

```
{output_dir}/
├── {job_id}/
│   ├── scene_images/           # 로컬 임시 폴더 (업로드 안함)
│   │   ├── scene_000.png
│   │   ├── scene_001.png
│   │   └── scene_002.png
│   ├── audio.mp3               # TTS 오디오
│   ├── video.mp4               # 최종 영상 (업로드됨)
│   ├── subtitles.srt           # 자막 (업로드됨)
│   └── thumb.jpg               # 썸네일 (업로드됨)
```

---

## 8. 테스트

### 8.1 테스트 파일

| 파일 | 테스트 수 | 설명 |
|------|-----------|------|
| `test_phase37_visual_render.py` | 34 | Phase 37 전체 테스트 |

### 8.2 테스트 카테고리

```python
# VisualPlan 추출 테스트 (11개)
TestVisualPlanExtractor:
  - test_extract_title_from_on_screen_text
  - test_extract_title_from_caption
  - test_extract_title_from_narration
  - test_extract_body_from_narration
  - test_extract_highlight_quoted_terms
  - test_extract_highlight_acronyms
  - test_extract_highlight_number_units
  - test_extract_highlight_emphasis_pattern
  - test_extract_max_highlight_terms
  - test_extract_all_scenes
  - test_singleton_instance

# ImageAssetService 테스트 (5개)
TestImageAssetService:
  - test_service_available
  - test_generate_scene_image_creates_file
  - test_generate_scene_image_naming
  - test_generate_all_scene_images
  - test_singleton_instance

# VideoComposer animated 모드 테스트 (6개)
TestVideoComposerAnimatedMode:
  - test_config_visual_style_default
  - test_config_visual_style_animated
  - test_config_fade_duration
  - test_config_kenburns_zoom
  - test_has_scene_images_true
  - test_has_scene_images_false_*

# RealVideoRenderer animated 통합 테스트 (3개)
TestRealVideoRendererAnimated:
  - test_render_slides_basic_mode_skips_image_gen
  - test_render_slides_animated_mode_generates_images
  - test_render_slides_image_directory_created

# Config 설정 테스트 (6개)
TestPhase37ConfigSettings:
  - test_video_visual_style_default
  - test_video_width/height/fps/fade_duration/kenburns_zoom_default

# 통합 파이프라인 테스트 (2개)
TestAnimatedPipelineIntegration:
  - test_visual_plan_to_image_pipeline
  - test_multiple_scenes_pipeline
```

### 8.3 테스트 실행

```bash
# Phase 37 테스트만 실행
python -m pytest tests/test_phase37_visual_render.py -v

# 결과: 34 passed
```

---

## 9. 사용 예시

### 9.1 Basic 모드 (기존)

```env
VIDEO_VISUAL_STYLE=basic
```

```
렌더링 결과: 단색 배경 + drawtext 오버레이
```

### 9.2 Animated 모드 (신규)

```env
VIDEO_VISUAL_STYLE=animated
VIDEO_FADE_DURATION=0.5
VIDEO_KENBURNS_ZOOM=1.1
```

```
렌더링 결과:
- 각 씬마다 그라데이션 배경 + 제목/본문/하이라이트 이미지 생성
- Ken Burns 줌 효과 (홀수 씬: 줌 인, 짝수 씬: 줌 아웃)
- 씬 간 0.5초 fade 전환
```

---

## 10. 제한사항 및 향후 개선

### 10.1 현재 제한사항

- Pillow 필수 (없으면 mock 모드로 동작)
- FFmpeg 필수 (없으면 mock 모드로 동작)
- 한글 폰트가 없으면 텍스트 렌더링 제한

### 10.2 향후 개선 가능 항목

- [ ] AI 이미지 생성 연동 (DALL-E, Stable Diffusion)
- [ ] 다양한 레이아웃 템플릿 지원
- [ ] 씬별 배경색 커스터마이징
- [ ] 애니메이션 효과 확장 (slide, wipe, dissolve 등)

---

## 11. 결론

Phase 37에서 "영상처럼 보이게" 하기 위한 최소 연출 기능을 구현했습니다.

- **VisualPlan**: SceneInfo → 시각적 요소 추출 (deterministic, LLM 불필요)
- **ImageAssetService**: Pillow로 씬 이미지 생성 (그라데이션 + 하이라이트)
- **VideoComposer**: FFmpeg zoompan + xfade로 Ken Burns + fade 효과
- **모드 전환**: VIDEO_VISUAL_STYLE 환경변수로 basic/animated 선택

백엔드 변경 없이, 기존 파이프라인에 최소한의 수정으로 영상 품질을 향상시켰습니다.
