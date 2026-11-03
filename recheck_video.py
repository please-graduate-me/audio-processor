"""DB를 조회하지 않고 기존 영상의 말소리 구간만 다시 분석한다."""
from pathlib import Path
import tempfile
from main import prepare_audio
from speech_filter import FILTER_VERSION, detect_speech


def inspect_video(youtube_id, wav=None):
    with tempfile.TemporaryDirectory(prefix="graduate-me-recheck-") as directory:
        if wav is None:
            _, _, _, wav = prepare_audio(youtube_id, Path(directory))
        regions = detect_speech(Path(wav))
        return {"type": "rechecked", "speechRegions": regions, "filterVersion": FILTER_VERSION}
