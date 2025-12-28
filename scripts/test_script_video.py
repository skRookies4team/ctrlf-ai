"""
script.json을 사용한 영상 생성 테스트

사용법:
1. 터미널에서 실행: python test_script_video.py
2. 결과: test_output_script/ 폴더에 영상 생성
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
else:
    print(f"[WARN] FFmpeg not found at {FFMPEG_PATH}")

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

# 환경변수 설정
os.environ.setdefault("AI_ENV", "mock")
os.environ["TTS_PROVIDER"] = "gtts"

from dataclasses import dataclass
from typing import Optional, List


@dataclass
class SceneInfo:
    """Scene info for video generation."""
    scene_id: int
    narration: str
    caption: Optional[str] = None
    on_screen_text: Optional[str] = None
    duration_sec: Optional[float] = None
    audio_path: Optional[str] = None
    image_path: Optional[str] = None


def load_script(script_path: str) -> dict:
    """Load script from JSON file."""
    with open(script_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def script_to_scenes(script: dict, max_scenes: int = None) -> List[SceneInfo]:
    """Convert script JSON to SceneInfo list."""
    scenes = []
    scene_counter = 0

    for chapter in script.get('chapters', []):
        chapter_title = chapter.get('title', '')

        for scene in chapter.get('scenes', []):
            scene_counter += 1

            if max_scenes and scene_counter > max_scenes:
                break

            scenes.append(SceneInfo(
                scene_id=scene_counter,
                narration=scene.get('narration', ''),
                caption=scene.get('caption', ''),
                on_screen_text=f"{chapter_title} - {scene.get('purpose', '')}",
                duration_sec=scene.get('duration_sec', 15),
            ))

        if max_scenes and scene_counter >= max_scenes:
            break

    return scenes


async def generate_video_from_script(
    script_path: str,
    output_dir: str = "./test_output_script",
    max_scenes: int = 4,  # 테스트용으로 최대 4개 씬만
):
    """Generate video from script.json."""
    import importlib.util

    def load_module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    base = Path(__file__).parent.parent / "app"

    # 모듈 로드
    vc_module = load_module("video_composer", base / "services" / "video_composer.py")
    VideoComposer = vc_module.VideoComposer
    ComposerConfig = vc_module.ComposerConfig

    vp_module = load_module("visual_plan", base / "services" / "visual_plan.py")
    VisualPlanExtractor = vp_module.VisualPlanExtractor

    ia_module = load_module("image_asset_service", base / "services" / "image_asset_service.py")
    ImageAssetService = ia_module.ImageAssetService

    tts_module = load_module("tts_provider", base / "clients" / "tts_provider.py")
    get_tts_provider = tts_module.get_tts_provider

    print("\n" + "=" * 60)
    print("Script-based Video Generation Test")
    print("=" * 60)

    # Output directory
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    # 1. Load script
    print(f"\n[1/6] Loading script: {script_path}")
    script = load_script(script_path)
    print(f"  -> Title: {script.get('title', 'Unknown')}")
    print(f"  -> Chapters: {len(script.get('chapters', []))}")

    # 2. Convert to scenes
    print(f"\n[2/6] Converting to scenes (max {max_scenes})...")
    scenes = script_to_scenes(script, max_scenes=max_scenes)
    print(f"  -> {len(scenes)} scenes created")
    for scene in scenes:
        print(f"     Scene {scene.scene_id}: {scene.on_screen_text} ({scene.duration_sec}s)")

    # 3. Extract VisualPlan
    print("\n[3/6] Extracting VisualPlan...")
    extractor = VisualPlanExtractor()
    plans = extractor.extract_all(scenes)
    for plan in plans:
        title_short = plan.title[:40] if len(plan.title) > 40 else plan.title
        print(f"  -> Scene {plan.scene_id}: '{title_short}'")

    # 4. Generate scene images
    print("\n[4/6] Generating scene images...")
    image_service = ImageAssetService()
    image_dir = output_path / "scene_images"
    image_dir.mkdir(exist_ok=True)

    for i, (scene, plan) in enumerate(zip(scenes, plans)):
        image_path = image_service.generate_scene_image(plan, image_dir, i)
        scene.image_path = image_path
        print(f"  -> Scene {scene.scene_id} image: {Path(image_path).name}")

    # 5. Generate TTS audio
    print("\n[5/6] Generating TTS audio...")
    tts = get_tts_provider()
    full_text = " ".join(scene.narration for scene in scenes)
    audio_path = output_path / "audio.mp3"

    try:
        duration = await tts.synthesize_to_file(
            text=full_text,
            output_path=audio_path,
            language="ko",
        )
        print(f"  -> Audio created: {audio_path.name}, duration: {duration:.1f}s")
    except Exception as e:
        print(f"  [WARN] TTS generation failed: {e}")
        print("  -> Continuing without audio")
        audio_path = None

    # 6. Video composition
    print("\n[6/6] Video composition...")
    config = ComposerConfig(
        visual_style="animated",
        fade_duration=0.5,
        kenburns_zoom=1.1,
    )
    composer = VideoComposer(config=config)

    if not composer.is_available:
        print("  [ERROR] FFmpeg not found.")
        print("  -> Install FFmpeg: winget install FFmpeg")
        return None

    try:
        result = await composer.compose(
            scenes=scenes,
            audio_path=str(audio_path) if audio_path and audio_path.exists() else None,
            output_dir=output_path,
            job_id="script-test",
        )
        print(f"\n{'=' * 60}")
        print("[SUCCESS] Video generation completed!")
        print(f"{'=' * 60}")
        print(f"  Video: {result.video_path}")
        print(f"  Subtitle: {result.subtitle_path}")
        print(f"  Thumbnail: {result.thumbnail_path}")
        print(f"  Duration: {result.duration_sec:.1f}s")
        print(f"\nOutput folder: {output_path.absolute()}")
        return result

    except Exception as e:
        print(f"\n[FAILED] Video composition failed: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    print("=" * 60)
    print("CTRL-F AI - Script-based Video Generation")
    print("=" * 60)

    script_path = Path(__file__).parent / "script.json"

    if not script_path.exists():
        print(f"[ERROR] Script file not found: {script_path}")
        sys.exit(1)

    # 테스트용으로 4개 씬만 생성 (전체는 시간 오래 걸림)
    # 전체 생성하려면 max_scenes=None 으로 변경
    asyncio.run(generate_video_from_script(
        script_path=str(script_path),
        output_dir="./test_output_script",
        max_scenes=4,  # 테스트: 4개 씬만 / 전체: None
    ))
