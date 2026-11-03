import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import sys
import httpx
from segmentation import split_transcript
from speech_filter import FILTER_VERSION, detect_speech
from errors import ProcessingError
from video_validation import validate_metadata, youtube_metadata


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def run(command, timeout):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ProcessingError("TIMEOUT", "영상 처리 시간이 초과되었습니다. 잠시 후 다시 시도합니다.", True)
    if result.returncode:
        details = result.stderr.lower()
        if any(value in details for value in ["private video", "not available", "removed", "unavailable", "age-restricted", "sign in", "confirm you’re not a bot", "confirm you're not a bot", "403", "po token"]):
            raise ProcessingError("EXTRACTION_BLOCKED", "이 영상의 오디오에 접근할 수 없습니다. 공개된 다른 영상을 선택하거나 추출기 설정을 확인해 주세요.")
        raise ProcessingError("EXTRACTION_FAILED", "오디오 추출 또는 변환에 실패했습니다. 잠시 후 다시 시도합니다.", True)
    return result.stdout


def prepare_audio(video_id, root, on_download=None):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{11}", video_id):
        raise ProcessingError("INVALID_VIDEO", "영상 ID가 올바르지 않습니다.")
    url = "https://www.youtube.com/watch?v=" + video_id
    base = ["yt-dlp", "--ignore-config", "--no-playlist", "--no-warnings", "--socket-timeout", "30", "--retries", "2", "--js-runtimes", "node", "--remote-components", "ejs:github"]
    if os.environ.get("YTDLP_COOKIES_FILE"):
        base += ["--cookies", os.environ["YTDLP_COOKIES_FILE"]]
    if os.environ.get("YTDLP_EXTRACTOR_ARGS"):
        base += ["--extractor-args", os.environ["YTDLP_EXTRACTOR_ARGS"]]
    api_info = youtube_metadata(video_id)
    info = json.loads(run(base + ["--dump-single-json", "--skip-download", "--", url], 120))
    duration = validate_metadata(info)
    if api_info:
        info["title"] = api_info["title"]
    info["youtubeCategoryId"] = api_info.get("categoryId") if api_info else None
    info["category"] = (api_info.get("category") if api_info else None) or next(iter(info.get("categories") or []), None)
    run(base + ["-f", "bestaudio/best", "--max-filesize", "100M", "-o", str(root / "source.%(ext)s"), "--", url], 240)
    sources = [path for path in root.glob("source.*") if path.suffix not in [".part", ".ytdl"]]
    if len(sources) != 1:
        raise ProcessingError("NO_AUDIO", "추출된 오디오를 찾을 수 없습니다.")
    if on_download:
        on_download()
    output = root / "audio.mp3"
    run(["ffmpeg", "-nostdin", "-y", "-i", str(sources[0]), "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", str(output)], 120)
    if output.stat().st_size > 25 * 1024 * 1024:
        raise ProcessingError("FILE_TOO_LARGE", "음성 인식 파일 크기 제한을 초과했습니다.")
    wav = root / "audio.wav"
    run(["ffmpeg", "-nostdin", "-y", "-i", str(sources[0]), "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], 120)
    return info, duration, output, wav


def process(youtube_id, emit):
    api_key = os.environ.get("GROQ_API_KEY") or os.environ.get("QRUQ_WHISPER")
    if not api_key:
        raise ProcessingError("MISSING_API_KEY", "음성 인식 API 키가 설정되지 않았습니다.")
    with tempfile.TemporaryDirectory(prefix="graduate-me-") as tmp:
        info, duration, output, wav = prepare_audio(youtube_id, Path(tmp), lambda: emit({"type": "downloaded"}))
        try:
            speech_regions = detect_speech(wav)
        except Exception:
            raise ProcessingError("VAD_FAILED", "말소리 감지에 실패했습니다. 워커의 VAD 모델과 실행 환경을 확인해 주세요.")
        if not speech_regions:
            raise ProcessingError("NO_SPEECH", "학습할 말소리를 찾지 못했습니다. 음악이나 무음 위주의 영상인지 확인해 주세요.")
        emit({"type": "transcribing", "title": str(info.get("title") or "유튜브 영상"), "duration": duration,
              "category": info.get("category"), "youtubeCategoryId": info.get("youtubeCategoryId")})
        with output.open("rb") as file:
            try:
                response = httpx.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": "Bearer " + api_key},
                    files={"file": ("audio.mp3", file, "audio/mpeg")},
                    data={"model": "whisper-large-v3", "language": "en", "response_format": "verbose_json", "temperature": "0", "timestamp_granularities[]": ["word", "segment"]}, timeout=180)
            except httpx.TransportError:
                raise ProcessingError("STT_NETWORK", "음성 인식 서버 연결에 실패했습니다. 잠시 후 다시 시도합니다.", True)
        if response.status_code in [401, 403]:
            raise ProcessingError("STT_AUTH", "Groq API 키와 접근 권한을 확인해 주세요.")
        if response.status_code == 429 or response.status_code >= 500:
            raise ProcessingError("STT_TEMPORARY", "음성 인식 서버가 혼잡하거나 사용량 제한에 도달했습니다.", True)
        if response.is_error:
            raise ProcessingError("STT_FAILED", "음성 인식 요청을 처리하지 못했습니다.")
        try:
            segments = split_transcript(response.json(), duration, speech_regions)
        except (ValueError, KeyError, TypeError):
            raise ProcessingError("INVALID_TRANSCRIPT", "학습 가능한 음성 또는 정확한 시간 정보를 얻지 못했습니다. 다른 영상을 선택해 주세요.")
        return {"type": "complete", "segments": segments, "speechRegions": speech_regions, "filterVersion": FILTER_VERSION}



def emit_event(event):
    # stdout은 NestJS와의 JSONL 계약에만 사용하고 진단 로그는 stderr로 보낸다.
    print(json.dumps({"version": 1, **event}, ensure_ascii=False, allow_nan=False), flush=True)


def execute(request, emit):
    youtube_id = request.get("youtubeId")
    if request.get("version") != 1 or not isinstance(youtube_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{11}", youtube_id):
        raise ProcessingError("INVALID_REQUEST", "음성 처리 요청 형식이 올바르지 않습니다.")
    if request.get("operation") == "transcribe":
        emit(process(youtube_id, emit))
    elif request.get("operation") == "recheck":
        from recheck_video import inspect_video
        emit(inspect_video(youtube_id, request.get("wav")))
    else:
        raise ProcessingError("INVALID_REQUEST", "지원하지 않는 음성 처리 작업입니다.")


def main():
    try:
        execute(json.loads(sys.stdin.readline()), emit_event)
    except ProcessingError as error:
        emit_event({"type": "error", "code": error.code, "message": str(error), "retryable": error.retryable})
        return 1
    except Exception:
        emit_event({"type": "error", "code": "INTERNAL", "message": "음성 처리 중 오류가 발생했습니다. 잠시 후 다시 시도합니다.", "retryable": True})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
