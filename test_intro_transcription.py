import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from main import prepare_transcription_audio, process
from segmentation import restore_timeline, split_transcript


class IntroTranscriptionTests(unittest.TestCase):
    @patch("main.run")
    def test_speech_at_start_keeps_original_audio(self, run):
        original = Path("audio.mp3")
        self.assertEqual(prepare_transcription_audio(original, Path("audio.wav"), [{"start": 0, "end": 2}]), (original, 0))
        run.assert_not_called()

    @patch("main.run")
    def test_leading_silence_keeps_extra_margin(self, run):
        trimmed, offset = prepare_transcription_audio(Path("audio.mp3"), Path("audio.wav"), [{"start": 5.56, "end": 12.488}])
        self.assertAlmostEqual(offset, 5.06)
        self.assertEqual(trimmed, Path("speech.mp3"))
        command = run.call_args.args[0]
        self.assertAlmostEqual(float(command[command.index("-ss") + 1]), offset)
        self.assertEqual(command[command.index("-i") + 1], "audio.wav")

    def test_word_and_confidence_times_share_original_timeline(self):
        payload = {
            "text": "This is crazy, Ben.",
            "words": [
                {"word": "This", "start": .5, "end": .65},
                {"word": "is", "start": .65, "end": .84},
                {"word": "crazy,", "start": .84, "end": 1.58},
                {"word": "Ben.", "start": 1.58, "end": 1.7},
            ],
            "segments": [{"start": .5, "end": 1.7, "no_speech_prob": .1, "avg_logprob": -.2}],
        }
        restored = restore_timeline(payload, 5.06)
        self.assertAlmostEqual(restored["segments"][0]["start"], 5.56)
        self.assertEqual(payload["words"][0]["start"], .5)
        segments = split_transcript(restored, 20, [{"start": 5.56, "end": 12.488}])
        self.assertEqual(segments[0]["text"], "This is crazy, Ben.")
        self.assertAlmostEqual(segments[0]["start"], 5.56)
        self.assertAlmostEqual(segments[0]["end"], 6.76)

    @patch.dict("os.environ", {"GROQ_API_KEY": "테스트용 키"})
    @patch("main.httpx.post")
    @patch("main.detect_speech", return_value=[{"start": 5.56, "end": 12.488}])
    def test_process_sends_trimmed_audio_and_restores_time(self, detect, post):
        post.return_value = Mock(status_code=200, is_error=False)
        post.return_value.json.return_value = {"words": [{"word": "Hello.", "start": .5, "end": 1}]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "audio.mp3"
            trimmed = Path(directory) / "speech.mp3"
            trimmed.write_bytes(b"trimmed-audio")
            with patch("main.prepare_audio", return_value=({}, 20, output, Path("audio.wav"))), patch("main.prepare_transcription_audio", return_value=(trimmed, 5.06)):
                result = process("VHZI9_we5GQ", lambda event: None)
                self.assertEqual(post.call_args.kwargs["files"]["file"][1].name, str(trimmed))
        self.assertEqual(result["segments"][0]["text"], "Hello.")
        self.assertAlmostEqual(result["segments"][0]["start"], 5.56)
        self.assertAlmostEqual(result["segments"][0]["end"], 6.06)


if __name__ == "__main__":
    unittest.main()
