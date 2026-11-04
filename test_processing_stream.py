"""실제 자식 프로세스로 종료 코드, 시간 초과와 취소 정리를 검증한다."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from processing_stream import stream_process

REQUEST = {"version": 1, "operation": "transcribe", "youtubeId": "JnfBXjWm7hc"}


class ProcessingStreamTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, code, **options):
        return [json.loads(line) async for line in stream_process(REQUEST, [sys.executable, "-c", code], **options)]

    async def test_success_is_returned_after_process_exit(self):
        events = await self.collect('import json; print(json.dumps({"version":1,"type":"complete","segments":[]}))')
        self.assertEqual(events[-1]["type"], "complete")

    async def test_crash_after_complete_is_not_success(self):
        events = await self.collect('import json; print(json.dumps({"version":1,"type":"complete"})); raise SystemExit(1)')
        self.assertEqual(events[-1]["code"], "PROCESSOR_FAILED")

    async def test_missing_result_is_not_success(self):
        events = await self.collect('pass')
        self.assertEqual(events[-1]["code"], "PROCESSOR_FAILED")

    async def test_domain_error_is_preserved(self):
        events = await self.collect('import json; print(json.dumps({"version":1,"type":"error","code":"NO_SPEECH","message":"말소리 없음","retryable":False})); raise SystemExit(1)')
        self.assertEqual(events[-1]["code"], "NO_SPEECH")
        self.assertFalse(events[-1]["retryable"])

    async def test_timeout_emits_heartbeat_and_stops_child(self):
        events = await self.collect('import time; time.sleep(10)', timeout=0.15, heartbeat=0.03)
        self.assertIn("heartbeat", [event["type"] for event in events])
        self.assertEqual(events[-1]["code"], "TIMEOUT")

    async def test_connection_close_removes_process_and_temp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "process.json"
            code = (
                "import json,os,time,pathlib; "
                f"pathlib.Path({str(marker)!r}).write_text(json.dumps({{'pid':os.getpid(),'tmp':os.environ['TMPDIR']}})); "
                "print(json.dumps({'version':1,'type':'downloaded'}),flush=True); time.sleep(30)"
            )
            stream = stream_process(REQUEST, [sys.executable, "-c", code])
            self.assertEqual(json.loads(await anext(stream))["type"], "downloaded")
            details = json.loads(marker.read_text())
            await stream.aclose()
            self.assertFalse(Path(details["tmp"]).exists())
            with self.assertRaises(ProcessLookupError):
                os.kill(details["pid"], 0)


if __name__ == "__main__":
    unittest.main()
