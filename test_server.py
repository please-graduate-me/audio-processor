"""외부 음성 API를 호출하지 않고 HTTP 인증·검증·동시 실행 제한을 검사한다."""
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import server
from processing_stream import encode_event

TOKEN = "internal-test-token-" * 3
REQUEST = {"version": 1, "operation": "transcribe", "youtubeId": "JnfBXjWm7hc"}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"PROCESSOR_API_TOKEN": TOKEN, "PROCESSOR_CONCURRENCY": "1"})
        self.env.start()
        self.client = TestClient(server.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()

    def test_health_is_available_without_credentials(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok", "version": 1})

    def test_missing_or_wrong_auth_never_starts_processing(self):
        with patch("server.stream_process") as process:
            for headers in [{}, {"Authorization": "Bearer wrong"}]:
                self.assertEqual(self.client.post("/v1/process", json=REQUEST, headers=headers).status_code, 401)
            process.assert_not_called()

    def test_invalid_id_version_and_remote_file_access_are_rejected(self):
        with patch("server.stream_process") as process:
            for body in [{**REQUEST, "youtubeId": "../bad"}, {**REQUEST, "version": 2}, {**REQUEST, "wav": "/etc/passwd"}]:
                response = self.client.post("/v1/process", json=body, headers={"Authorization": "Bearer " + TOKEN})
                self.assertEqual(response.status_code, 422)
            process.assert_not_called()

    def test_streaming_response_and_capacity_release(self):
        async def processing(payload):
            self.assertEqual(payload, REQUEST)
            yield encode_event({"type": "downloaded"})
            yield encode_event({"type": "error", "code": "NO_SPEECH", "message": "말소리 없음", "retryable": False})
        with patch("server.stream_process", processing):
            for _ in range(2):
                response = self.client.post("/v1/process", json=REQUEST, headers={"Authorization": "Bearer " + TOKEN})
                self.assertEqual(response.status_code, 200)
                self.assertIn("application/x-ndjson", response.headers["content-type"])
                self.assertEqual([json.loads(line)["type"] for line in response.text.splitlines()], ["downloaded", "error"])

    def test_busy_server_returns_retryable_http_status(self):
        with self.client.portal.wrap_async_context_manager(server.app.state.slots):
            response = self.client.post("/v1/process", json=REQUEST, headers={"Authorization": "Bearer " + TOKEN})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "15")


if __name__ == "__main__":
    unittest.main()
