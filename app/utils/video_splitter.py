from typing import Dict, List

MAX_VIDEO_SEC = 180.0  # HeyGen 제한


def split_video_inputs_by_duration(video_inputs: List[dict]) -> List[List[dict]]:
    groups = []
    current = []
    acc = 0.0

    for v in video_inputs:
        dur = v.get("metadata", {}).get("duration_sec", 30)

        if acc + dur > MAX_VIDEO_SEC and current:
            groups.append(current)
            current = []
            acc = 0.0

        current.append(v)
        acc += dur

    if current:
        groups.append(current)

    return groups
