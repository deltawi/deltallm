"""Fixed one-token provider for disposable local gateway measurements."""

import asyncio
import json
from time import monotonic

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

app = FastAPI()
stream_events: list[dict[str, object]] = []
batch_gate: asyncio.Event | None = None


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [{"id": "fixed-one-token", "object": "model", "owned_by": "fixture"}],
    }


@app.get("/fixture/stream-events")
async def events():
    return stream_events


@app.post("/fixture/batch-hold")
async def hold_batch():
    global batch_gate
    if batch_gate is not None and not batch_gate.is_set():
        raise HTTPException(409, "Batch fixture is already held")
    batch_gate = asyncio.Event()
    return {"held": True}


@app.post("/fixture/batch-release")
async def release_batch():
    if batch_gate is not None:
        batch_gate.set()
    return {"held": False}


class CompletionRequest(BaseModel):
    model: str
    messages: list[dict[str, object]] = Field(max_length=4)
    max_tokens: int
    stream: bool = False


@app.post("/v1/chat/completions")
async def complete(request: CompletionRequest):
    if request.model != "fixed-one-token" or request.max_tokens != 1:
        raise HTTPException(400, "This fixture supports only the fixed one-token workload")
    if request.stream:

        async def chunks():
            chunk = {
                "id": "fixed-stream",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": request.model,
                "choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": None}],
            }
            yield "data: " + json.dumps(chunk) + "\n\n"
            # Fixed long stream exceeds the default 50-second response cutoff.
            # Cancellation closes this generator; no terminal success is emitted.
            try:
                await asyncio.sleep(300)
            finally:
                if len(stream_events) < 128:
                    stream_events.append(
                        {"event": "upstream_closed", "monotonic_seconds": monotonic()}
                    )
            chunk["choices"][0].update(delta={}, finish_reason="stop")
            chunk["usage"] = {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}
            yield "data: " + json.dumps(chunk) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")
    if request.messages == [{"role": "user", "content": "Lifecycle batch fixture."}]:
        if batch_gate is not None:
            await asyncio.wait_for(batch_gate.wait(), timeout=180)
        await asyncio.sleep(10)
    return {
        "id": "fixed-local-completion",
        "object": "chat.completion",
        "created": 1,
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "OK"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
    }
