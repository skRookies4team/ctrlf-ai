# Phase 32: Video Rendering Realization

**작성일**: 2025-12-19
**Phase**: 32
**상태**: 완료
**테스트 결과**: 25 passed

---

## 1. 개요

Phase 32에서는 Phase 27의 Mock 렌더러를 실제 렌더링 파이프라인으로 전환하고, 렌더 진행률을 WebSocket으로 실시간 전달하는 기능을 구현했습니다.

### 1.1 목표

- **A) TTS Provider 어댑터**: 실제 음성 합성 (Mock/gTTS/AWS Polly/Google Cloud TTS)
- **B) Video Composer (FFmpeg)**: 영상 합성 + SRT 자막 생성
- **C) Storage 어댑터**: 에셋 업로드 (Local/S3/MinIO)
- **D) WebSocket Progress**: 렌더 진행률 실시간 전달

---

## 2. 구현 상세

### 2.1 TTS Provider 어댑터

**파일**: `app/clients/tts_provider.py`

```python
# 인터페이스
class BaseTTSProvider(ABC):
    async def synthesize(text, voice, speed, language) -> TTSResult
    async def synthesize_to_file(text, output_path, ...) -> float (duration)

# 구현체
- MockTTSProvider: 테스트용 Mock
- GTTSProvider: Google TTS (무료, pip install gtts)
- PollyTTSProvider: AWS Polly (pip install boto3)
- GCPTTSProvider: Google Cloud TTS (pip install google-cloud-texttospeech)
```

**환경변수**:
```bash
TTS_PROVIDER=mock  # mock | gtts | polly | gcp

# AWS Polly 사용 시
AWS_REGION=ap-northeast-2
AWS_ACCESS_KEY_ID=xxx
AWS_SECRET_ACCESS_KEY=xxx

# Google Cloud TTS 사용 시
GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json
```

### 2.2 Storage 어댑터

**파일**: `app/clients/storage_adapter.py`

```python
# 인터페이스
class BaseStorageProvider(ABC):
    async def put_object(data, key, content_type) -> StorageResult
    async def get_url(key, expires_in) -> str
    async def delete_object(key) -> bool

# 구현체
- LocalStorageProvider: 로컬 파일 시스템 (개발용)
- S3StorageProvider: AWS S3
- MinIOStorageProvider: MinIO (S3 호환)
```

**환경변수**:
```bash
STORAGE_PROVIDER=local  # local | s3 | minio
STORAGE_LOCAL_PATH=./video_output
STORAGE_BASE_URL=http://localhost:8000/static/videos

# S3 사용 시
AWS_S3_BUCKET=my-bucket
AWS_S3_REGION=ap-northeast-2
AWS_S3_PREFIX=videos/

# MinIO 사용 시
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=videos
```

### 2.3 Video Composer (FFmpeg)

**파일**: `app/services/video_composer.py`

```python
class VideoComposer:
    async def compose(scenes, audio_path, output_dir, job_id) -> ComposedVideo
    async def get_audio_duration(audio_path) -> float

# 출력물
- video.mp4: 합성된 영상
- subtitle.srt: SRT 자막 파일
- thumbnail.jpg: 썸네일 이미지
```

**FFmpeg 요구사항**:
```bash
# Ubuntu/Debian
apt-get install ffmpeg

# macOS
brew install ffmpeg

# Windows
# ffmpeg.exe를 PATH에 추가

# Docker
FROM python:3.12
RUN apt-get update && apt-get install -y ffmpeg
```

### 2.4 WebSocket Progress

**파일**: `app/api/v1/ws_render_progress.py`

**엔드포인트**: `WS /ws/videos/{video_id}/render-progress`

**이벤트 형식**:
```json
{
    "job_id": "job-xxx",
    "video_id": "video-xxx",
    "status": "RUNNING",
    "step": "GENERATE_TTS",
    "progress": 25,
    "message": "음성 합성 중...",
    "timestamp": "2025-12-19T10:30:00Z"
}
```

**단계별 진행률**:

| 단계 | 시작 | 종료 | 메시지 |
|------|------|------|--------|
| VALIDATE_SCRIPT | 0% | 5% | 스크립트 검증 중... |
| GENERATE_TTS | 5% | 30% | 음성 합성 중... |
| GENERATE_SUBTITLE | 30% | 40% | 자막 생성 중... |
| RENDER_SLIDES | 40% | 55% | 슬라이드 렌더링 중... |
| COMPOSE_VIDEO | 55% | 85% | 영상 합성 중... |
| UPLOAD_ASSETS | 85% | 95% | 에셋 업로드 중... |
| FINALIZE | 95% | 100% | 마무리 중... |

**프론트엔드 사용 예시**:
```javascript
const ws = new WebSocket("ws://localhost:8000/ws/videos/video-001/render-progress");

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log(`Progress: ${data.progress}% - ${data.message}`);

    if (data.status === "SUCCEEDED") {
        console.log("렌더링 완료!");
        ws.close();
    }
};

// Ping/Pong 유지
setInterval(() => ws.send("ping"), 30000);
```

### 2.5 Real Video Renderer

**파일**: `app/services/video_renderer_real.py`

```python
class RealVideoRenderer(VideoRenderer):
    """실제 TTS, FFmpeg, Storage를 사용하는 렌더러."""

    def __init__(
        self,
        config: RealRendererConfig,
        tts_provider: BaseTTSProvider,
        storage_provider: BaseStorageProvider,
        video_composer: VideoComposer,
    )

    async def execute_step(step, script_json, job_id) -> None
    async def get_rendered_assets(job_id) -> RenderedAssets
```

---

## 3. 환경변수 (Phase 32 추가)

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `TTS_PROVIDER` | `mock` | TTS 제공자 (mock/gtts/polly/gcp) |
| `STORAGE_PROVIDER` | `local` | Storage 제공자 (local/s3/minio) |
| `RENDER_OUTPUT_DIR` | `./video_output` | 렌더링 출력 디렉토리 |
| `STORAGE_BASE_URL` | `http://localhost:8000/static/videos` | 로컬 스토리지 기본 URL |
| `AWS_S3_BUCKET` | - | S3 버킷 이름 |
| `AWS_S3_REGION` | `ap-northeast-2` | S3 리전 |
| `AWS_S3_PREFIX` | `videos/` | S3 키 프리픽스 |
| `MINIO_ENDPOINT` | - | MinIO 엔드포인트 |
| `MINIO_BUCKET` | `videos` | MinIO 버킷 이름 |

---

## 4. 사용 예시 (curl)

### 4.1 WebSocket 연결 (wscat)

```bash
# wscat 설치
npm install -g wscat

# WebSocket 연결
wscat -c ws://localhost:8000/ws/videos/video-001/render-progress
```

### 4.2 렌더 잡 생성 및 진행률 확인

```bash
# 1. 스크립트 생성
curl -X POST http://localhost:8000/api/scripts \
  -H "Content-Type: application/json" \
  -d '{
    "video_id": "video-001",
    "raw_json": {
      "chapters": [{
        "chapter_id": 1,
        "title": "테스트",
        "scenes": [{
          "scene_id": 1,
          "narration": "테스트 나레이션입니다.",
          "on_screen_text": "테스트"
        }]
      }]
    }
  }'

# 2. 스크립트 승인
curl -X POST http://localhost:8000/api/scripts/{script_id}/approve

# 3. 렌더 잡 생성 (WebSocket으로 진행률 수신)
curl -X POST http://localhost:8000/api/videos/video-001/render-jobs \
  -H "Content-Type: application/json" \
  -d '{"script_id": "{script_id}"}'
```

---

## 5. 테스트 결과

**파일**: `tests/test_phase32_video_rendering.py`

| 테스트 클래스 | 테스트 수 | 설명 |
|-------------|----------|------|
| `TestTTSProvider` | 5 | TTS Provider 선택 및 합성 |
| `TestStorageAdapter` | 6 | Storage 업로드/삭제/URL |
| `TestVideoComposer` | 5 | FFmpeg 합성 및 SRT 생성 |
| `TestWebSocketProgress` | 3 | 진행률 이벤트 및 매핑 |
| `TestIntegration` | 3 | 통합 테스트 |
| `TestRegression` | 3 | 기존 API 회귀 테스트 |

```bash
# 테스트 실행
python -m pytest tests/test_phase32_video_rendering.py -v

# 결과
25 passed in 3.63s
```

---

## 6. 변경된 파일

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `app/clients/tts_provider.py` | 신규 | TTS Provider 어댑터 |
| `app/clients/storage_adapter.py` | 신규 | Storage 어댑터 |
| `app/services/video_composer.py` | 신규 | FFmpeg Video Composer |
| `app/services/video_renderer_real.py` | 신규 | 실제 렌더러 구현 |
| `app/api/v1/ws_render_progress.py` | 신규 | WebSocket Progress 엔드포인트 |
| `app/core/config.py` | 수정 | Phase 32 환경변수 추가 |
| `app/main.py` | 수정 | WebSocket 라우터 등록 |
| `tests/test_phase32_video_rendering.py` | 신규 | Phase 32 테스트 25개 |
| `docs/DEVELOPMENT_REPORT_PHASE32.md` | 신규 | Phase 32 개발 리포트 |

---

## 7. 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                     VideoRenderService                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                  RealVideoRenderer                       │   │
│  │                                                          │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │   │
│  │  │ TTS Provider│  │   Video     │  │    Storage      │  │   │
│  │  │             │  │  Composer   │  │    Adapter      │  │   │
│  │  │ - Mock      │  │  (FFmpeg)   │  │ - Local         │  │   │
│  │  │ - gTTS      │  │             │  │ - S3            │  │   │
│  │  │ - Polly     │  │ - MP4       │  │ - MinIO         │  │   │
│  │  │ - GCP TTS   │  │ - SRT       │  │                 │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────────┘  │   │
│  │                          │                               │   │
│  │                          ▼                               │   │
│  │         ┌─────────────────────────────────┐             │   │
│  │         │   WebSocket Progress Manager    │             │   │
│  │         │   (실시간 진행률 브로드캐스트)    │             │   │
│  │         └─────────────────────────────────┘             │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Frontend (React)                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  WebSocket Client                                        │   │
│  │  ws://localhost:8000/ws/videos/{video_id}/render-progress│   │
│  │                                                          │   │
│  │  ┌──────────────────────────────────────┐               │   │
│  │  │  Progress Bar: [████████░░░░░] 65%   │               │   │
│  │  │  Status: 영상 합성 중...              │               │   │
│  │  └──────────────────────────────────────┘               │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 8. Docker 설정

```dockerfile
FROM python:3.12-slim

# FFmpeg 설치
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 추가 TTS 라이브러리 (선택)
RUN pip install gtts  # 무료 Google TTS
# RUN pip install boto3  # AWS Polly
# RUN pip install google-cloud-texttospeech  # Google Cloud TTS

COPY . /app
WORKDIR /app

ENV TTS_PROVIDER=gtts
ENV STORAGE_PROVIDER=local

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 9. 향후 계획

1. **영상 템플릿 시스템**: 배경 이미지/애니메이션 템플릿 적용
2. **썸네일 자동 생성 개선**: AI 기반 최적 프레임 선택
3. **다중 음성 지원**: 씬별 다른 음성 사용
4. **영상 미리보기**: 렌더링 전 미리보기 기능
5. **CDN 통합**: CloudFront 등 CDN 연동

---

**작성자**: Claude Code
**검토자**: -
