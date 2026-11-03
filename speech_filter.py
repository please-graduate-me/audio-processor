"""원본 시간축의 말소리 구간을 찾고 전사 후보를 검증한다."""
import math
import wave
from functools import lru_cache
import numpy as np
import onnxruntime as ort
from download_vad import MODEL_PATH, verify_model

SAMPLE_RATE = 16000
WINDOW = 512
FILTER_VERSION = 1


@lru_cache(maxsize=1)
def model_session():
    verify_model()
    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return ort.InferenceSession(str(MODEL_PATH), sess_options=options, providers=["CPUExecutionProvider"])


def probability_regions(probabilities, duration, threshold=0.5, min_speech=0.25, min_silence=0.2, padding=0.2):
    """짧은 휴지는 이어 붙이고 앞뒤 여백으로 약한 어두와 어미를 보호한다."""
    step = WINDOW / SAMPLE_RATE
    regions = []
    start = None
    silence_start = None
    for index, probability in enumerate(probabilities):
        time = index * step
        if probability >= threshold:
            if start is None:
                start = time
            silence_start = None
        elif start is not None and probability < threshold - 0.15:
            if silence_start is None:
                silence_start = time
            if time + step - silence_start >= min_silence:
                if silence_start - start >= min_speech:
                    regions.append({"start": max(0, start - padding), "end": min(duration, silence_start + padding)})
                start = None
                silence_start = None
    if start is not None:
        end = min(duration, silence_start if silence_start is not None else duration)
        if end - start >= min_speech:
            regions.append({"start": max(0, start - padding), "end": min(duration, end + padding)})
    merged = []
    for region in regions:
        if merged and region["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], region["end"])
        else:
            merged.append(region)
    return merged


def detect_speech(wav_path):
    with wave.open(str(wav_path), "rb") as audio:
        if audio.getframerate() != SAMPLE_RATE or audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise ValueError("VAD 입력은 16kHz 모노 16비트 PCM이어야 합니다.")
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
    if not len(samples):
        return []
    session = model_session()
    # Silero v6.2.1의 ONNX 입력 규약: 상태 2×1×128, 이전 문맥 64샘플.
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, 64), dtype=np.float32)
    probabilities = []
    for offset in range(0, len(samples), WINDOW):
        chunk = samples[offset:offset + WINDOW]
        if len(chunk) < WINDOW:
            chunk = np.pad(chunk, (0, WINDOW - len(chunk)))
        inputs = np.concatenate((context, chunk.reshape(1, WINDOW)), axis=1)
        probability, state = session.run(None, {"input": inputs, "state": state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)})
        probabilities.append(float(probability.item()))
        context = inputs[:, -64:]
    return probability_regions(probabilities, len(samples) / SAMPLE_RATE)


def overlap_seconds(start, end, regions):
    return sum(max(0, min(end, region["end"]) - max(start, region["start"])) for region in regions)


def has_speech(start, end, regions):
    return end > start and overlap_seconds(start, end, regions) / (end - start) >= 0.25


def low_confidence_regions(payload):
    result = []
    for segment in payload.get("segments", []):
        try:
            probability, confidence = float(segment["no_speech_prob"]), float(segment["avg_logprob"])
            start, end = float(segment["start"]), float(segment["end"])
        except (TypeError, ValueError, KeyError):
            continue
        if all(math.isfinite(value) for value in [probability, confidence, start, end]) and end > start and probability > 0.6 and confidence < -1.0:
            result.append({"start": start, "end": end})
    return result


def unsupported_transcript_regions(payload, speech_regions):
    result = []
    for segment in payload.get("segments", []):
        try:
            start, end = float(segment["start"]), float(segment["end"])
        except (TypeError, ValueError, KeyError):
            continue
        if math.isfinite(start) and math.isfinite(end) and end > start and not has_speech(start, end, speech_regions):
            result.append({"start": start, "end": end})
    return result


def accept_word(word, speech_regions, suspicious_regions):
    start, end = word["start"], word["end"]
    if not has_speech(start, end, speech_regions):
        return False
    # 무음 확률과 낮은 인식 신뢰도가 동시에 나타난 구간만 추가로 제외한다.
    return not any(max(0, min(end, region["end"]) - max(start, region["start"])) >= (end - start) / 2 for region in suspicious_regions)
