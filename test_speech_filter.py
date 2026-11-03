import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
import wave
import numpy as np
from segmentation import split_transcript
from speech_filter import detect_speech, probability_regions, has_speech, low_confidence_regions
from main import process
from errors import ProcessingError


class SpeechFilterTests(unittest.TestCase):
    def test_silence_and_short_noise_do_not_create_regions(self):
        self.assertEqual(probability_regions([0.01] * 100, 3.2), [])
        self.assertEqual(probability_regions([0.9] * 3 + [0.01] * 97, 3.2), [])

    def test_immediate_speech_and_final_speech_keep_original_times(self):
        regions = probability_regions([0.9] * 20, .64)
        self.assertEqual(regions, [{"start": 0, "end": .64}])
        regions = probability_regions([0.01] * 100 + [0.9] * 20, 3.84)
        self.assertAlmostEqual(regions[0]["start"], 3.0)
        self.assertAlmostEqual(regions[0]["end"], 3.84)

    def test_padding_and_short_pauses_protect_word_boundaries(self):
        regions = probability_regions([0.9] * 20 + [0.01] * 3 + [0.9] * 20, 1.376)
        self.assertEqual(len(regions), 1)
        self.assertTrue(has_speech(.01, .05, regions))
        self.assertFalse(has_speech(8, 9, regions))

    def test_hallucinated_intro_removed_without_shifting_words(self):
        payload = {"text": "Reviewer. Hello world!", "words": [
            {"word": "Reviewer", "start": 0, "end": 4},
            {"word": "Hello", "start": 16, "end": 17},
            {"word": "world", "start": 17, "end": 18},
        ]}
        result = split_transcript(payload, 20, [{"start": 15.8, "end": 18.2}])
        self.assertEqual(result[0]["text"], "Hello world!")
        self.assertEqual((result[0]["start"], result[0]["end"]), (16, 18))
        self.assertEqual(result[0]["index"], 0)

    def test_short_intro_false_positive_does_not_validate_whole_sentence(self):
        payload = {"segments": [{"start": 0, "end": 7}], "words": [
            {"word": "Reviewer", "start": .04, "end": 2.24},
            {"word": "Hello", "start": 16, "end": 17},
        ]}
        regions = [{"start": 0, "end": .84}, {"start": 15.8, "end": 17.2}]
        self.assertFalse(has_speech(.04, 4.32, regions))
        result = split_transcript(payload, 20, regions)
        self.assertEqual([segment["text"] for segment in result], ["Hello"])

    def test_confidence_requires_both_signals(self):
        for probability, confidence, should_exclude in [(.9, -1.5, True), (.9, -.2, False), (.1, -2, False)]:
            payload = {"segments": [{"start": 0, "end": 2, "no_speech_prob": probability, "avg_logprob": confidence}], "words": [{"word": "Hello", "start": 0, "end": 1}]}
            self.assertEqual(bool(low_confidence_regions(payload)), should_exclude)
            if should_exclude:
                with self.assertRaises(ValueError):
                    split_transcript(payload, 2, [{"start": 0, "end": 2}])
            else:
                self.assertEqual(len(split_transcript(payload, 2, [{"start": 0, "end": 2}])), 1)

    def test_real_model_rejects_silence_and_synthetic_music(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            samples = np.arange(3 * 16000) / 16000
            chord = sum(np.sin(2 * np.pi * hz * samples) for hz in [261.6, 329.6, 392.0]) * .06
            for audio in [np.zeros_like(samples), chord]:
                with wave.open(str(path), "wb") as file:
                    file.setnchannels(1); file.setsampwidth(2); file.setframerate(16000)
                    file.writeframes((audio * 32767).astype("<i2").tobytes())
                self.assertEqual(detect_speech(path), [])

    @patch.dict("os.environ", {"GROQ_API_KEY": "테스트용 키"})
    @patch("main.httpx.post")
    @patch("main.detect_speech", return_value=[])
    @patch("main.prepare_audio", return_value=({}, 20, Path("audio.mp3"), Path("audio.wav")))
    def test_no_speech_never_calls_stt(self, prepare, detect, post):
        with self.assertRaises(ProcessingError) as caught:
            process("JnfBXjWm7hc", Mock())
        self.assertEqual(caught.exception.code, "NO_SPEECH")
        post.assert_not_called()

    @patch.dict("os.environ", {"GROQ_API_KEY": "테스트용 키"})
    @patch("main.httpx.post")
    @patch("main.detect_speech", side_effect=RuntimeError("모델 없음"))
    @patch("main.prepare_audio", return_value=({}, 20, Path("audio.mp3"), Path("audio.wav")))
    def test_vad_failure_does_not_bypass_filter(self, prepare, detect, post):
        with self.assertRaises(ProcessingError) as caught:
            process("JnfBXjWm7hc", Mock())
        self.assertEqual(caught.exception.code, "VAD_FAILED")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
