"""Optional local sidecar adapter for the baseline visual-support consumer."""
from .local_files import plain_path
from .visual_scene_signal import load_visual_scene_signals, normalize_visual_scene_signals


def visual_support(run, vod):
    """Read only this VOD's bounded sidecars; never open frame/OCR references."""
    rows = []
    for name in ('visual_scene_signal.jsonl', 'visual_scene_signal.json'):
        path = plain_path(run / name)
        if path.is_file():
            if path.stat().st_size > 2_000_000:
                raise ValueError('Visual support sidecar exceeds 2 MB')
            rows.extend(load_visual_scene_signals(path))
    if len(rows) > 5000:
        raise ValueError('Visual support sidecar exceeds 5000 rows')
    rows = normalize_visual_scene_signals(rows, video_no=str(vod.video_no), duration_sec=vod.duration)
    if any(row['video_no'] != str(vod.video_no) for row in rows):
        raise ValueError('Visual support belongs to another VOD')
    return [row for row in rows if row['status'] == 'ok']
