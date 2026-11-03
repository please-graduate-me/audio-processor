"""고정 버전의 공개 VAD 모델을 내려받고 해시를 검증한다."""
import hashlib
from pathlib import Path
import httpx

MODEL_PATH = Path(__file__).parent / "models" / "silero_vad.onnx"
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
MODEL_URL = "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/src/silero_vad/data/silero_vad.onnx"


def verify_model(path=MODEL_PATH):
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
        raise RuntimeError("VAD 모델이 없거나 손상되었습니다. python worker/download_vad.py를 실행해 주세요.")


def download():
    if MODEL_PATH.is_file():
        verify_model()
        return
    response = httpx.get(MODEL_URL, timeout=60, follow_redirects=True)
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != MODEL_SHA256:
        raise RuntimeError("다운로드한 VAD 모델의 해시가 일치하지 않습니다.")
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = MODEL_PATH.with_suffix(".download")
    temporary.write_bytes(response.content)
    temporary.replace(MODEL_PATH)


if __name__ == "__main__":
    download()
    print("VAD 모델 준비 완료")
