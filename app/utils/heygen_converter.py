def convert_to_heygen_script(video_script: dict) -> dict:
    """
    ctrlf-ai video script → HeyGen-friendly script
    """
    heygen_scenes = []

    for c_idx, chapter in enumerate(video_script.get("chapters", []), start=1):
        for s_idx, scene in enumerate(chapter.get("scenes", []), start=1):
            narration = scene.get("narration", "").strip()
            if not narration:
                continue

            heygen_scenes.append({
                "scene_id": f"{c_idx}-{s_idx}",
                "speaker": "female_kr_1",  # 나중에 옵션화 가능
                "text": narration,
            })

    return {
        "title": video_script.get("title", "교육 영상"),
        "scenes": heygen_scenes,
    }
