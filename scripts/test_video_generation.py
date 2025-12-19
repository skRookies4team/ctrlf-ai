"""
영상 생성 테스트 스크립트

사용법:
1. 서버 실행: uvicorn app.main:app --reload --port 8000
2. 이 스크립트 실행: python test_video_generation.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# 콘솔 인코딩 설정
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# FFmpeg 경로 설정 (winget 설치 경로)
FFMPEG_PATH = Path.home() / "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.0.1-full_build/bin"
if FFMPEG_PATH.exists():
    os.environ["PATH"] = str(FFMPEG_PATH) + os.pathsep + os.environ.get("PATH", "")
    os.environ["FFMPEG_PATH"] = str(FFMPEG_PATH / "ffmpeg.exe")
    os.environ["FFPROBE_PATH"] = str(FFMPEG_PATH / "ffprobe.exe")
    print(f"[OK] FFmpeg path: {FFMPEG_PATH}")

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).parent))

# 환경변수 설정 (import 전에)
os.environ.setdefault("AI_ENV", "mock")
os.environ["TTS_PROVIDER"] = "gtts"  # gTTS 사용

# 필요한 모듈만 직접 임포트
from dataclasses import dataclass
from typing import Optional

@dataclass
class SceneInfo:
    """Scene info for testing."""
    scene_id: int
    narration: str
    caption: Optional[str] = None
    on_screen_text: Optional[str] = None
    duration_sec: Optional[float] = None
    audio_path: Optional[str] = None
    image_path: Optional[str] = None


async def test_simple_video():
    """Simple video generation test"""
    # 직접 모듈 임포트 (app.services.__init__ 우회)
    import importlib.util

    def load_module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    base = Path(__file__).parent / "app"

    # video_composer 모듈 로드
    vc_module = load_module("video_composer", base / "services" / "video_composer.py")
    VideoComposer = vc_module.VideoComposer
    ComposerConfig = vc_module.ComposerConfig

    # visual_plan 모듈 로드
    vp_module = load_module("visual_plan", base / "services" / "visual_plan.py")
    VisualPlanExtractor = vp_module.VisualPlanExtractor

    # image_asset_service 모듈 로드
    ia_module = load_module("image_asset_service", base / "services" / "image_asset_service.py")
    ImageAssetService = ia_module.ImageAssetService

    # tts_provider 모듈 로드
    tts_module = load_module("tts_provider", base / "clients" / "tts_provider.py")
    get_tts_provider = tts_module.get_tts_provider

    print("\n" + "="*60)
    print("Video Generation Test")
    print("="*60)

    # Output directory
    output_dir = Path("./test_output")
    output_dir.mkdir(exist_ok=True)

    # 1. Create test scenes
    print("\n[1/5] Creating scene data...")
    scenes = [
        SceneInfo(
            scene_id=1,
            narration="Hello. Let's learn about USB memory. USB stands for Universal Serial Bus.",
            caption="USB Memory Introduction",
            duration_sec=5.0,
        ),
        SceneInfo(
            scene_id=2,
            narration="USB memory capacity ranges from 16GB to 128GB. Price starts from about 5000 won.",
            caption="USB Capacity and Price",
            duration_sec=5.0,
        ),
        SceneInfo(
            scene_id=3,
            narration="To safely remove USB memory, use the Safely Remove Hardware feature.",
            caption="Safe Usage",
            duration_sec=5.0,
        ),
    ]
    print(f"  -> {len(scenes)} scenes created")

    # 2. Extract VisualPlan
    print("\n[2/5] Extracting VisualPlan...")
    extractor = VisualPlanExtractor()
    plans = extractor.extract_all(scenes)
    for plan in plans:
        title_short = plan.title[:30] if len(plan.title) > 30 else plan.title
        print(f"  -> Scene {plan.scene_id}: title='{title_short}', highlights={plan.highlight_terms}")

    # 3. Generate scene images
    print("\n[3/5] Generating scene images...")
    image_service = ImageAssetService()
    image_dir = output_dir / "scene_images"
    image_dir.mkdir(exist_ok=True)

    for i, (scene, plan) in enumerate(zip(scenes, plans)):
        image_path = image_service.generate_scene_image(plan, image_dir, i)
        scene.image_path = image_path
        print(f"  -> Scene {scene.scene_id} image: {Path(image_path).name}")

    # 4. Generate TTS audio
    print("\n[4/5] Generating TTS audio...")
    tts = get_tts_provider()
    full_text = " ".join(scene.narration for scene in scenes)
    audio_path = output_dir / "audio.mp3"

    try:
        duration = await tts.synthesize_to_file(
            text=full_text,
            output_path=audio_path,
            language="ko",
        )
        print(f"  -> Audio created: {audio_path.name}, duration: {duration:.1f}s")
    except Exception as e:
        print(f"  [WARN] TTS generation failed: {e}")
        print("  -> Using mock audio")
        duration = 15.0

    # 5. Video composition
    print("\n[5/5] Video composition...")
    config = ComposerConfig(
        visual_style="animated",
        fade_duration=0.5,
        kenburns_zoom=1.1,
    )
    composer = VideoComposer(config=config)

    if not composer.is_available:
        print("  [WARN] FFmpeg not found.")
        print("  -> Restart terminal or add to PATH:")
        print(f"     {FFMPEG_PATH}")
        return

    try:
        result = await composer.compose(
            scenes=scenes,
            audio_path=str(audio_path) if audio_path.exists() else None,
            output_dir=output_dir,
            job_id="test-job",
        )
        print(f"\n{'='*60}")
        print("[SUCCESS] Video generation completed!")
        print(f"{'='*60}")
        print(f"  Video: {result.video_path}")
        print(f"  Subtitle: {result.subtitle_path}")
        print(f"  Thumbnail: {result.thumbnail_path}")
        print(f"  Duration: {result.duration_sec:.1f}s")
        print(f"\nOutput folder: {output_dir.absolute()}")

    except Exception as e:
        print(f"\n[FAILED] Video composition failed: {e}")
        import traceback
        traceback.print_exc()


async def test_basic_video():
    """Basic mode (solid background) test"""
    import importlib.util

    def load_module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    base = Path(__file__).parent / "app"
    vc_module = load_module("video_composer", base / "services" / "video_composer.py")
    VideoComposer = vc_module.VideoComposer
    ComposerConfig = vc_module.ComposerConfig
    tts_module = load_module("tts_provider", base / "clients" / "tts_provider.py")
    get_tts_provider = tts_module.get_tts_provider

    print("\n" + "="*60)
    print("Basic Mode Video Generation Test")
    print("="*60)

    output_dir = Path("./test_output_basic")
    output_dir.mkdir(exist_ok=True)

    scenes = [
        SceneInfo(
            scene_id=1,
            narration="This is a basic mode test.",
            caption="Basic Mode",
            duration_sec=3.0,
        ),
    ]

    # TTS
    tts = get_tts_provider()
    audio_path = output_dir / "audio.mp3"
    try:
        await tts.synthesize_to_file("This is a basic mode test.", audio_path, "en")
    except:
        pass

    # Basic mode composition
    config = ComposerConfig(visual_style="basic")
    composer = VideoComposer(config=config)

    if not composer.is_available:
        print("FFmpeg not found - skipping")
        return

    result = await composer.compose(
        scenes=scenes,
        audio_path=str(audio_path) if audio_path.exists() else None,
        output_dir=output_dir,
        job_id="test-basic",
    )
    print(f"[OK] Basic mode completed: {result.video_path}")


if __name__ == "__main__":
    print("="*60)
    print("CTRL-F AI 영상 생성 테스트")
    print("="*60)

    # Animated 모드 테스트
    asyncio.run(test_simple_video())
