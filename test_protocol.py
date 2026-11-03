"""NestJS와의 처리 계약을 외부 API 호출 없이 검증한다."""
import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from main import execute, main, process
from errors import ProcessingError


class ProtocolTests(unittest.TestCase):
    def test_invalid_request_never_starts_processing(self):
        with patch("main.process") as processing:
            for request in [
                {"version": 2, "operation": "transcribe", "youtubeId": "JnfBXjWm7hc"},
                {"version": 1, "operation": "transcribe", "youtubeId": "../invalid"},
                {"version": 1, "operation": "unknown", "youtubeId": "JnfBXjWm7hc"},
            ]:
                with self.assertRaises(ProcessingError):
                    execute(request, Mock())
            processing.assert_not_called()

    def test_error_is_versioned_json_without_traceback(self):
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"version":1,"youtubeId":"invalid"}\n')), patch("sys.stdout", output):
            self.assertEqual(main(), 1)
        event = json.loads(output.getvalue())
        self.assertEqual((event["version"], event["type"], event["retryable"]), (1, "error", False))

    @patch.dict("os.environ", {"GROQ_API_KEY": "테스트용 키"})
    def test_success_returns_segments_without_database(self):
        events = []
        response = Mock(status_code=200, is_error=False)
        response.json.return_value = {"words": [{"word": "Hello.", "start": 1, "end": 2}]}
        def prepare(youtube_id, root, on_download):
            output = root / "audio.mp3"
            output.write_bytes(b"audio")
            on_download()
            return {"title": "음성 처리 테스트", "category": "교육", "youtubeCategoryId": "27"}, 3, output, root / "audio.wav"
        with patch("main.prepare_audio", side_effect=prepare), patch("main.detect_speech", return_value=[{"start": 0, "end": 3}]), patch("main.httpx.post", return_value=response):
            execute({"version": 1, "operation": "transcribe", "youtubeId": "JnfBXjWm7hc"}, events.append)
        self.assertEqual([event["type"] for event in events], ["downloaded", "transcribing", "complete"])
        self.assertEqual(events[-1]["segments"][0]["text"], "Hello.")

    @patch.dict("os.environ", {"GROQ_API_KEY": "테스트용 키"})
    def test_rate_limit_remains_retryable(self):
        response = Mock(status_code=429)
        with patch("main.prepare_audio", return_value=({}, 3, Path(__file__), Path("audio.wav"))), patch("main.detect_speech", return_value=[{"start": 0, "end": 3}]), patch("main.httpx.post", return_value=response):
            with self.assertRaises(ProcessingError) as caught:
                process("JnfBXjWm7hc", Mock())
        self.assertEqual(caught.exception.code, "STT_TEMPORARY")
        self.assertTrue(caught.exception.retryable)

    def test_recheck_never_calls_transcription(self):
        with patch("main.process") as transcribe, patch("recheck_video.inspect_video", return_value={"type": "rechecked"}) as inspect:
            events = []
            execute({"version": 1, "operation": "recheck", "youtubeId": "JnfBXjWm7hc", "wav": "/tmp/audio.wav"}, events.append)
        inspect.assert_called_once_with("JnfBXjWm7hc", "/tmp/audio.wav")
        transcribe.assert_not_called()
        self.assertEqual(events, [{"type": "rechecked"}])


if __name__ == "__main__":
    unittest.main()
