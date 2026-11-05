import math
import re
from speech_filter import accept_word, low_confidence_regions, unsupported_transcript_regions


def restore_timeline(payload, offset):
    """잘라낸 초반 무음만큼 단어와 신뢰도 구간을 원본 영상 시간으로 복원한다."""
    return {**payload, **{
        key: [{**item, "start": float(item["start"]) + offset, "end": float(item["end"]) + offset} for item in payload.get(key, [])]
        for key in ("words", "segments")
    }}


def split_transcript(payload, duration, speech_regions=None):
    """단어의 원본 시간을 보존하면서 문장, 휴지, 최대 길이로 나눈다."""
    raw = payload.get("words") or []
    if not raw:
        raise ValueError("단어 타임스탬프가 없어 학습 구절을 만들 수 없습니다.")
    words = []
    previous_end = 0.0
    for item in raw:
        text = str(item.get("word", "")).strip()
        start, end = float(item["start"]), float(item["end"])
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ValueError("음성 인식 시간 정보가 올바르지 않습니다.")
        if not text or not re.search(r"[a-zA-Z0-9]", text):
            continue
        start, end = max(0, start, previous_end), min(duration, end)
        if end <= start:
            continue
        if end - start > 12:
            raise ValueError("단어 타임스탬프의 길이가 너무 깁니다.")
        words.append({"word": text, "start": start, "end": end})
        previous_end = end
    # 문장부호가 전사 본문에만 있으면 단어 순서가 일치할 때 복원한다.
    text_words = str(payload.get("text", "")).split()
    normalize = lambda word: re.sub(r"[^a-z0-9']", "", word.lower().replace("’", "'"))
    if len(text_words) == len(words) and all(normalize(a) == normalize(b["word"]) for a, b in zip(text_words, words)):
        for item, text in zip(words, text_words):
            item["word"] = text
    if speech_regions is not None:
        suspicious = low_confidence_regions(payload) + unsupported_transcript_regions(payload, speech_regions)
        words = [word for word in words if accept_word(word, speech_regions, suspicious)]
    result, group = [], []

    def flush():
        if group:
            result.append({"index": len(result), "start": group[0]["start"], "end": group[-1]["end"], "text": " ".join(word["word"] for word in group), "words": list(group)})
            group.clear()

    for word in words:
        if group and (len(group) >= 25 or word["end"] - group[0]["start"] > 12 or word["start"] - group[-1]["end"] >= 0.7):
            flush()
        group.append(word)
        if re.search(r"[.!?][\"'’)]?$", word["word"]):
            flush()
    flush()
    if not result:
        raise ValueError("학습 가능한 영어 음성을 찾지 못했습니다.")
    return result
