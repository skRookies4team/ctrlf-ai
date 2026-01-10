# Heygen 영상 생성 사용 가이드

> 작성일: 2025-01-09
> 대상: CTRLF AI 팀
> 목적: Heygen API를 활용한 AI 영상 생성 시스템 사용 가이드

---

## 목차

1. [개요](#1-개요)
2. [시스템 아키텍처](#2-시스템-아키텍처)
3. [파일 구조 및 역할](#3-파일-구조-및-역할)
4. [주요 기능 설명](#4-주요-기능-설명)
5. [사용 방법](#5-사용-방법)
6. [환경 설정](#6-환경-설정)
7. [API 참고](#7-api-참고)
8. [주의사항 및 문제해결](#8-주의사항-및-문제해결)

---

## 1. 개요

### 1.1 Heygen 소개

Heygen은 AI 기반 아바타 영상 생성 서비스로, 텍스트 입력을 통해 자연스러운 아바타 영상을 생성합니다.

### 1.2 시스템 목적

- CTRLF AI 영상 생성 파이프라인에서 **외부 렌더링 엔진**으로 활용
- FFmpeg 기반 내부 렌더링의 대안으로 고품질 AI 아바타 영상 생성
- 챕터 단위로 영상을 생성하여 최종 편집에 활용

### 1.3 주요 특징

- **고품질 아바타**: 다양한 아바타 캐릭터 지원
- **자연스러운 TTS**: 한국어 음성 합성
- **비동기 처리**: 긴 영상도 배치 처리 가능
- **실시간 상태 모니터링**: 진행률 및 상태 확인

---

## 2. 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                  CTRLF AI Video Pipeline                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐       │
│  │ VideoScript │ -> │   HeyGen   │ -> │   Status    │       │
│  │   (JSON)    │    │  Payload   │    │ Monitoring  │       │
│  └─────────────┘    └─────────────┘    └─────────────┘       │
│          │                    │                    │         │
│          ▼                    ▼                    ▼         │
│  LLM Generated     HeyGen API v2       Polling + Callback    │
│  Script Chapters   Video Generation    Real-time Updates     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 파일 구조 및 역할

### 3.1 주요 파일 목록

```
ctrlf-ai/
├── scripts/
│   ├── render_with_heygen.py           # ★ 메인 렌더링 스크립트
│   ├── check_heygen_status.py          # 상태 확인 스크립트
│   └── test_heygen_*.py                # 테스트 파일들
│
├── app/
│   ├── clients/
│   │   └── heygen_client.py            # ★ HeyGen API 클라이언트
│   │
│   ├── utils/
│   │   ├── heygen_payload.py           # ★ Payload 빌더 (v2 API)
│   │   └── heygen_converter.py         # 스크립트 변환 유틸
│   │
│   └── adapters/
│       └── heygen_script_adapter.py    # 스크립트 어댑터
```

> ★ 표시: 핵심 파일 (반드시 이해 필요)

### 3.2 파일별 역할 상세

#### `scripts/render_with_heygen.py` (메인 스크립트)

**역할**: 챕터 단위 Heygen 영상 생성 및 상태 모니터링

**주요 기능**:

- VideoScript JSON → Heygen Payload 변환
- HeyGen API 호출 및 영상 생성 요청
- 실시간 상태 폴링 (최대 30분)
- 결과 저장 및 콜백

#### `app/clients/heygen_client.py` (API 클라이언트)

**역할**: HeyGen API와의 모든 통신 처리

**주요 메서드**:

```python
class HeyGenClient:
    async def generate_video(payload: Dict) -> str  # 영상 생성 요청
    async def get_video_status(video_id: str) -> Dict  # 상태 조회
```

#### `app/utils/heygen_payload.py` (Payload 빌더)

**역할**: CTRLF VideoScript → HeyGen v2 API Payload 변환

**주요 함수**:

```python
def build_heygen_video_inputs(video_script, avatar_id, voice_id, ...) -> List[Dict]
def build_heygen_generate_payload(video_inputs, width, height) -> Dict
```

---

## 4. 주요 기능 설명

### 4.1 VideoScript → HeyGen 변환

```python
# 입력: CTRLF VideoScript JSON
{
  "chapters": [
    {
      "chapter_id": 1,
      "title": "피싱 공격이란?",
      "scenes": [
        {
          "scene_id": 1,
          "narration": "안녕하세요...",
          "on_screen_text": "피싱 공격의 정의",
          "duration_sec": 15.0
        }
      ]
    }
  ]
}

# 출력: HeyGen API Payload
{
  "video_inputs": [
    {
      "character": {
        "type": "avatar",
        "avatar_id": "avatar_id",
        "avatar_style": "normal"
      },
      "voice": {
        "type": "text",
        "input_text": "안녕하세요...",
        "voice_id": "voice_id"
      },
      "background": {
        "type": "color",
        "value": "#FAFAFA"
      },
      "metadata": {
        "chapter_id": 1,
        "scene_id": 1,
        "on_screen_text": "피싱 공격의 정의"
      }
    }
  ],
  "dimension": {
    "width": 1280,
    "height": 720
  }
}
```

### 4.2 영상 생성 플로우

```
1. 스크립트 로드
   ↓
2. Payload 변환 (build_heygen_video_inputs)
   ↓
3. HeyGen API 호출 (generate_video)
   ↓
4. Video ID 획득
   ↓
5. 상태 폴링 시작 (get_video_status)
   ↓
6. 완료 대기 (COMPLETED/FAILED)
   ↓
7. 결과 저장 및 콜백
```

### 4.3 상태 모니터링

**상태 값**:

- `PENDING`: 대기 중
- `PROCESSING`: 생성 중
- `COMPLETED`: 완료됨
- `FAILED`: 실패

**폴링 정책**:

- 간격: 10초
- 최대 대기: 30분 (180회)
- 타임아웃: 120초 (요청별)

---

## 5. 사용 방법

### 5.1 기본 사용법

```bash
# 1. 환경변수 설정
export HEYGEN_API_KEY="your_api_key"
export HEYGEN_AVATAR_ID="avatar_id"
export HEYGEN_VOICE_ID="voice_id"

# 2. 스크립트 실행 (챕터 1번 렌더링)
python scripts/render_with_heygen.py --chapter 1
```

### 5.2 고급 사용법

```bash
# 특정 환경변수로 실행
HEYGEN_BG_TYPE="color" \
HEYGEN_BG_VALUE="#FFFFFF" \
HEYGEN_DIM_W="1920" \
HEYGEN_DIM_H="1080" \
python scripts/render_with_heygen.py --chapter 2
```

### 5.3 상태 확인

```bash
# 특정 영상 상태 확인
python scripts/check_heygen_status.py
# (VIDEO_ID는 스크립트 내 하드코딩)
```

### 5.4 테스트 실행

```bash
# 기본 API 테스트
python heygen_test.py

# 고급 기능 테스트
python heygen_test_v1.py
```

---

## 6. 환경 설정

### 6.1 필수 환경변수

```bash
# HeyGen API 설정
HEYGEN_API_KEY=sk_V2_xxx  # 필수: API 키
HEYGEN_AVATAR_ID=Jin_Blue_Casual_Side_public  # 필수: 아바타 ID
HEYGEN_VOICE_ID=04515ba5ae2e431386807be5df246e72  # 필수: 음성 ID

# 선택 환경변수
HEYGEN_BG_TYPE=color  # 배경 타입 (color/image)
HEYGEN_BG_VALUE=#FAFAFA  # 배경 값
HEYGEN_DIM_W=1280  # 영상 너비
HEYGEN_DIM_H=720  # 영상 높이
```

### 6.2 환경변수 설명

| 변수               | 기본값    | 설명                    |
| ------------------ | --------- | ----------------------- |
| `HEYGEN_API_KEY`   | -         | HeyGen API 키 (필수)    |
| `HEYGEN_AVATAR_ID` | -         | 아바타 캐릭터 ID (필수) |
| `HEYGEN_VOICE_ID`  | -         | 음성 ID (필수)          |
| `HEYGEN_BG_TYPE`   | `color`   | 배경 타입               |
| `HEYGEN_BG_VALUE`  | `#FAFAFA` | 배경 값                 |
| `HEYGEN_DIM_W`     | `1280`    | 영상 너비               |
| `HEYGEN_DIM_H`     | `720`     | 영상 높이               |

### 6.3 .env 파일 예시

```bash
# .env 파일
HEYGEN_API_KEY=sk_V2_your_actual_key_here
HEYGEN_AVATAR_ID=Jin_Blue_Casual_Side_public
HEYGEN_VOICE_ID=04515ba5ae2e431386807be5df246e72
HEYGEN_BG_TYPE=color
HEYGEN_BG_VALUE=#FFFFFF
HEYGEN_DIM_W=1280
HEYGEN_DIM_H=720
```

---

## 7. API 참고

### 7.1 HeyGen API 엔드포인트

#### Video Generate (POST /v2/video/generate)

```python
# 요청
{
  "video_inputs": [
    {
      "character": {
        "type": "avatar",
        "avatar_id": "string",
        "avatar_style": "normal"
      },
      "voice": {
        "type": "text",
        "input_text": "string",  # 최대 5000자
        "voice_id": "string"
      },
      "background": {
        "type": "color",
        "value": "#FFFFFF"
      }
    }
  ],
  "dimension": {
    "width": 1280,
    "height": 720
  }
}

# 응답
{
  "data": {
    "video_id": "string"
  }
}
```

#### Video Status (GET /v1/video_status.get)

```python
# 요청 파라미터
video_id = "video_id_here"

# 응답
{
  "data": {
    "status": "COMPLETED",  # PENDING | PROCESSING | COMPLETED | FAILED
    "video_url": "https://...",  # 완료 시 제공
    "error": "에러 메시지"  # 실패 시 제공
  }
}
```

### 7.2 CTRLF 클라이언트 API

#### HeyGenClient 주요 메서드

```python
class HeyGenClient:
    def __init__(self, api_key: str):
        # API 키로 초기화

    async def generate_video(self, payload: Dict[str, Any]) -> str:
        """영상 생성 요청 및 video_id 반환"""

    async def get_video_status(self, video_id: str) -> Dict[str, Any]:
        """영상 상태 조회 (재시도 로직 포함)"""
```

---

## 8. 주의사항 및 문제해결

### 8.1 제한사항

- **텍스트 길이**: input_text 최대 5000자
- **영상 길이**: HeyGen 정책에 따라 제한될 수 있음 (180초 이내 권장)
- **API 제한**: Rate limiting 고려

### 8.2 에러 처리

#### 일반적인 에러 상황

```python
# API 키 오류
ValueError: HeyGen API key is required

# 네트워크 타임아웃
httpx.ReadTimeout: Request timed out

# API 에러 응답
RuntimeError: Unexpected response: {...}
```

#### 복구 방법

```python
# 1. API 키 확인
echo $HEYGEN_API_KEY

# 2. 네트워크 연결 확인
curl -H "X-Api-Key: $HEYGEN_API_KEY" https://api.heygen.com/v1/video_status.get?video_id=test

# 3. 환경변수 재설정
source .env
```

### 8.3 성능 최적화 팁

1. **배치 처리**: 여러 챕터를 순차적으로 처리
2. **상태 캐싱**: 완료된 영상은 재요청하지 않음
3. **타임아웃 조정**: 긴 영상은 폴링 간격 조정

### 8.4 모니터링 및 로깅

```python
# 로그 확인
tail -f logs/heygen_render.log

# 진행률 모니터링
# WebSocket 또는 주기적 상태 확인으로 구현
```

### 8.5 개발 시 주의사항

- **비용 관리**: HeyGen API는 유료 서비스
- **테스트 우선**: 운영 전 반드시 테스트 환경에서 검증
- **에러 핸들링**: 모든 외부 API 호출에 try-catch 적용
- **타임아웃 설정**: 긴 작업에 적절한 타임아웃 적용

---

## 부록: 샘플 코드

### A.1 간단한 테스트

```python
import asyncio
from app.clients.heygen_client import HeyGenClient

async def test_heygen():
    client = HeyGenClient("your_api_key")

    payload = {
        "video_inputs": [{
            "character": {
                "type": "avatar",
                "avatar_id": "Jin_Blue_Casual_Side_public"
            },
            "voice": {
                "voice_id": "04515ba5ae2e431386807be5df246e72",
                "type": "text",
                "input_text": "안녕하세요. 테스트 영상입니다."
            },
            "background": {
                "type": "color",
                "value": "#FFFFFF"
            }
        }],
        "dimension": {"width": 1280, "height": 720}
    }

    video_id = await client.generate_video(payload)
    print(f"Video ID: {video_id}")

    # 상태 모니터링
    while True:
        status = await client.get_video_status(video_id)
        print(f"Status: {status['data']['status']}")

        if status['data']['status'] == 'COMPLETED':
            print(f"Video URL: {status['data']['video_url']}")
            break

        await asyncio.sleep(10)

asyncio.run(test_heygen())
```

---

_이 문서는 실제 Heygen 코드 분석을 기반으로 작성되었습니다._
