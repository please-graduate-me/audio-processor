"""요청별 분석 프로세스를 격리하고 연결 종료 시 하위 도구와 임시 파일을 정리한다."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

import anyio

MAX_OUTPUT_BYTES = 8 * 1024 * 1024


def encode_event(event):
    return (json.dumps({"version": 1, **event}, ensure_ascii=False, allow_nan=False) + "\n").encode()


async def stop_process(process):
    if process is None:
        return
    # Python이 먼저 끝나도 같은 그룹에 남아 있는 추출 프로세스까지 종료한다.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def stream_process(payload, command=None, timeout=900, heartbeat=10):
    process = None
    pending = None
    terminal = None
    total = 0
    deadline = asyncio.get_running_loop().time() + timeout
    # 강제 종료로 Python의 finally가 실행되지 않아도 요청 단위 파일은 서버가 지운다.
    with tempfile.TemporaryDirectory(prefix="audio-request-") as directory:
        try:
            environment = dict(os.environ, TMPDIR=directory, PYTHONUNBUFFERED="1")
            environment.pop("PROCESSOR_API_TOKEN", None)
            process = await asyncio.create_subprocess_exec(
                *(command or [sys.executable, str(Path(__file__).with_name("main.py"))]),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=environment,
                start_new_session=True,
                limit=MAX_OUTPUT_BYTES,
            )
            process.stdin.write((json.dumps(payload) + "\n").encode())
            await process.stdin.drain()
            process.stdin.close()
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError()
                if pending is None:
                    pending = asyncio.create_task(process.stdout.readline())
                ready, _ = await asyncio.wait([pending], timeout=min(heartbeat, remaining))
                if not ready:
                    yield encode_event({"type": "heartbeat"})
                    continue
                line = pending.result()
                pending = None
                if not line:
                    break
                total += len(line)
                if total > MAX_OUTPUT_BYTES or terminal is not None:
                    raise ValueError("처리 출력 계약 위반")
                event = json.loads(line)
                if event.get("version") != 1:
                    raise ValueError("지원하지 않는 처리 버전")
                if event.get("type") in ("complete", "rechecked", "error"):
                    terminal = event
                elif event.get("type") in ("downloaded", "transcribing"):
                    yield encode_event(event)
                else:
                    raise ValueError("지원하지 않는 처리 이벤트")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError()
            code = await asyncio.wait_for(process.wait(), remaining)
            # 성공 이벤트를 출력한 뒤 충돌한 프로세스의 결과는 완료로 보내지 않는다.
            if terminal is None or (code != 0 and terminal["type"] != "error"):
                raise ValueError("정상적인 처리 결과 없이 종료")
            yield encode_event(terminal)
        except TimeoutError:
            yield encode_event({"type": "error", "code": "TIMEOUT", "message": "음성 처리 시간이 초과되었습니다.", "retryable": True})
        except Exception:
            yield encode_event({"type": "error", "code": "PROCESSOR_FAILED", "message": "음성 처리 프로그램 실행에 실패했습니다.", "retryable": True})
        finally:
            # HTTP 연결 취소의 취소 범위 안에서도 정리 작업은 끝까지 수행한다.
            with anyio.CancelScope(shield=True):
                if pending is not None:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await stop_process(process)
