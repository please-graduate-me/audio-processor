import math
import os
import re
import httpx
from errors import ProcessingError


def validate_metadata(info):
    duration = info.get("duration")
    if info.get("is_live") or info.get("live_status") in ["is_live", "is_upcoming", "post_live"] or info.get("_type") == "playlist":
        raise ProcessingError("UNSUPPORTED_VIDEO", "라이브 영상과 재생목록은 지원하지 않습니다.")
    if info.get("age_limit", 0) > 0 or info.get("availability") not in [None, "public", "unlisted"]:
        raise ProcessingError("RESTRICTED_VIDEO", "인증 없이 재생할 수 있는 공개 영상을 선택해 주세요.")
    if not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration <= 0 or duration > 900:
        raise ProcessingError("DURATION_LIMIT", "15분 이하의 영상만 지원합니다.")
    return float(duration)


def youtube_metadata(video_id):
    key = os.environ.get("YOUTUBE_API_KEY") or os.environ.get("YOUTUBE_DATA_API_v3")
    if not key:
        return None
    try:
        response = httpx.get("https://www.googleapis.com/youtube/v3/videos", params={"part": "snippet,contentDetails,status", "id": video_id}, headers={"X-Goog-Api-Key": key}, timeout=30)
    except httpx.TransportError:
        raise ProcessingError("YOUTUBE_NETWORK", "YouTube 영상 정보를 가져오지 못했습니다.", True)
    if response.is_error:
        raise ProcessingError("YOUTUBE_API", "YouTube Data API 키, API 사용 설정 또는 할당량을 확인해 주세요.", response.status_code >= 500 or response.status_code == 429)
    items = response.json().get("items", [])
    if not items:
        raise ProcessingError("VIDEO_UNAVAILABLE", "삭제되었거나 공개되지 않은 영상입니다.")
    item = items[0]
    if not item.get("status", {}).get("embeddable", False):
        raise ProcessingError("EMBED_DISABLED", "이 영상은 외부 사이트에서 재생할 수 없습니다.")
    if item.get("snippet", {}).get("liveBroadcastContent") in ["live", "upcoming"]:
        raise ProcessingError("UNSUPPORTED_VIDEO", "라이브와 예정된 영상은 지원하지 않습니다.")
    parts = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", item.get("contentDetails", {}).get("duration", ""))
    if not parts:
        raise ProcessingError("DURATION_LIMIT", "영상 길이를 확인할 수 없습니다.")
    hours, minutes, seconds = [int(value or 0) for value in parts.groups()]
    duration = hours * 3600 + minutes * 60 + seconds
    if not 0 < duration <= 900:
        raise ProcessingError("DURATION_LIMIT", "15분 이하의 영상만 지원합니다.")
    category_id = item.get("snippet", {}).get("categoryId")
    category = None
    if category_id:
        # 보조 정보 조회 실패가 정상 영상의 학습 준비를 막지 않게 한다.
        try:
            category_response = httpx.get("https://www.googleapis.com/youtube/v3/videoCategories", params={"part": "snippet", "id": category_id, "hl": "ko"}, headers={"X-Goog-Api-Key": key}, timeout=10)
            if not category_response.is_error:
                categories = category_response.json().get("items", [])
                if categories:
                    category = categories[0].get("snippet", {}).get("title")
        except (httpx.TransportError, ValueError, KeyError, TypeError):
            pass
    return {"title": item["snippet"]["title"], "duration": duration, "categoryId": category_id, "category": category}
