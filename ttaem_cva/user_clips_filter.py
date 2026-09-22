from __future__ import annotations

def is_eligible_in_vod_clip(clip: "UserClip", target_video_no: str | int) -> bool:
    """`clip` 이 `target_video_no` 의 in-VOD evidence 로 사용 가능한지.

    6-clause filter (모두 만족해야 True):
      1. `offset_status == "ok"` — anchor 회수 성공
      2. `str(video_no) == str(target_video_no)` — wrong-VOD 차단
      3. `offset_sec is not None` — anchor 시각 존재 (0 valid)
      4. `manual_override != "ignore"` — admin 제외 미적용
      5. `creation_window_status == "in_window"` — 방송 시간창 검증
      6. offset 이 0 이상이며 알려진 VOD 길이 안에 있음

    이 predicate가 offset 숫자성과 VOD 길이 상한까지 공통으로 검사하므로 LLM,
    보고서, Cafe 등 호출 경로가 서로 다른 안전선을 적용하지 않는다.
    """
    if (getattr(clip, "offset_status", "") or "") != "ok":
        return False
    if str(getattr(clip, "video_no", "") or "") != str(target_video_no or ""):
        return False
    if getattr(clip, "offset_sec", None) is None:
        return False
    if (getattr(clip, "creation_window_status", "") or "") != "in_window":
        return False
    try:
        offset_sec = int(getattr(clip, "offset_sec"))
    except (TypeError, ValueError):
        return False
    if offset_sec < 0:
        return False
    duration = getattr(clip, "source_vod_duration_sec", None)
    if duration is not None:
        try:
            if int(duration) > 0 and offset_sec > int(duration):
                return False
        except (TypeError, ValueError):
            return False
    if (getattr(clip, "manual_override", None) or "") == "ignore":
        return False
    return True

def is_eligible_unanchored_channel_signal(clip: "UserClip") -> bool:
    """`clip` 이 unanchored channel-popularity signal 후보인지.

    3-clause filter (모두 만족해야 True):
      1. `engagement_status == "ok"` — likes/plays 추출 성공
      2. `offset_status == "no_anchor"` — 클립 제작자가 원본 VOD 노출 OFF 한 케이스
         (wrong_vod 는 engagement nulled by `network.py` contamination guard;
          ok 는 anchored path 사용; unavailable 은 의미론 분리 — Codex MoA review)
      3. `manual_override != "ignore"` — admin 제외 미적용

    threshold (play_count 절대치 / bundle 내 ratio) 는 본 predicate 가 아닌
    formatter 가 적용. predicate 는 candidate set 만 정의.
    """
    if (getattr(clip, "engagement_status", "") or "") != "ok":
        return False
    if (getattr(clip, "offset_status", "") or "") != "no_anchor":
        return False
    if (getattr(clip, "creation_window_status", "") or "") != "in_window":
        return False
    if (getattr(clip, "manual_override", None) or "") == "ignore":
        return False
    return True
