"""NestJS 작업 관리자가 사용하는 내부 음성 처리 HTTP API다."""
import asyncio
from contextlib import aclosing, asynccontextmanager
import hmac
import os
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from download_vad import verify_model
from processing_stream import stream_process


class ProcessingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    operation: Literal["transcribe", "recheck"]
    youtubeId: str = Field(pattern=r"^[a-zA-Z0-9_-]{11}$")


@asynccontextmanager
async def lifespan(app):
    token = os.environ.get("PROCESSOR_API_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("PROCESSOR_API_TOKEN은 32자 이상으로 설정해야 합니다.")
    verify_model()
    capacity = int(os.environ.get("PROCESSOR_CONCURRENCY", "1"))
    if capacity < 1 or capacity > 8:
        raise RuntimeError("PROCESSOR_CONCURRENCY는 1~8이어야 합니다.")
    app.state.token = token
    app.state.slots = asyncio.Semaphore(capacity)
    yield


app = FastAPI(title="음성 처리 API", version="1.0.0", lifespan=lifespan)


def authenticate(authorization: str | None = Header(default=None)):
    expected = "Bearer " + app.state.token
    if authorization is None or not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="처리 API 인증이 필요합니다.", headers={"WWW-Authenticate": "Bearer"})


@app.get("/health")
async def health():
    return {"status": "ok", "version": 1}


@app.post("/v1/process", dependencies=[Depends(authenticate)])
async def process_request(payload: ProcessingRequest):
    slots = app.state.slots
    if slots.locked():
        raise HTTPException(status_code=429, detail="음성 처리 작업이 진행 중입니다.", headers={"Retry-After": "15"})
    await slots.acquire()
    released = False

    def release():
        nonlocal released
        if not released:
            released = True
            slots.release()

    async def events():
        try:
            async with aclosing(stream_process(payload.model_dump())) as stream:
                async for event in stream:
                    yield event
        finally:
            release()

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        background=BackgroundTask(release),
    )
