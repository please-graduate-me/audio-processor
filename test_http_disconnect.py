"""실제 HTTP 연결을 끊었을 때 처리 프로세스와 실행 슬롯이 정리되는지 확인한다."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import httpx

TOKEN = "http-integration-token-" * 3


class HttpDisconnectTests(unittest.TestCase):
    def test_disconnect_stops_processing_and_releases_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "process.json"
            analysis = (
                "import os,json,time,pathlib; "
                f"pathlib.Path({str(marker)!r}).write_text(json.dumps({{'pid':os.getpid(),'tmp':os.environ['TMPDIR']}})); "
                "print(json.dumps({'version':1,'type':'downloaded'}),flush=True); time.sleep(60)"
            )
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            launcher = (
                "import functools,sys,uvicorn,server; "
                f"server.stream_process=functools.partial(server.stream_process,command=[sys.executable,'-c',{analysis!r}]); "
                f"uvicorn.run(server.app,host='127.0.0.1',port={port},log_level='error',timeout_graceful_shutdown=1)"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", launcher],
                env=dict(os.environ, PROCESSOR_API_TOKEN=TOKEN, PROCESSOR_CONCURRENCY="1"),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3, headers={"Authorization": "Bearer " + TOKEN}) as client:
                    for _ in range(100):
                        try:
                            if client.get("/health").status_code == 200:
                                break
                        except httpx.TransportError:
                            time.sleep(0.05)
                    else:
                        self.fail("테스트 API 서버가 준비되지 않았습니다.")
                    payload = {"version": 1, "operation": "transcribe", "youtubeId": "JnfBXjWm7hc"}
                    with client.stream("POST", "/v1/process", json=payload) as response:
                        self.assertEqual(response.status_code, 200)
                        lines = response.iter_lines()
                        self.assertEqual(json.loads(next(lines))["type"], "downloaded")
                        details = json.loads(marker.read_text())
                        self.assertEqual(client.post("/v1/process", json=payload).status_code, 429)
                    for _ in range(100):
                        if not Path(details["tmp"]).exists():
                            break
                        time.sleep(0.03)
                    self.assertFalse(Path(details["tmp"]).exists())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(details["pid"], 0)
                    with client.stream("POST", "/v1/process", json=payload) as response:
                        self.assertEqual(response.status_code, 200)
                        lines = response.iter_lines()
                        self.assertEqual(json.loads(next(lines))["type"], "downloaded")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    unittest.main()
