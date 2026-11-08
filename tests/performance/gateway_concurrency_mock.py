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
# Held streams end early only when a test releases them. Streams started after
# a release wait on a fresh event and are held again.
stream_release = asyncio.Event()


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {"id": "fixed-one-token", "object": "model", "owned_by": "fixture"},
            {"id": "fixed-embedding", "object": "model", "owned_by": "fixture"},
        ],
    }


@app.get("/fixture/stream-events")
async def events():
    return stream_events


@app.post("/fixture/stream-release")
async def release_streams():
    """End every currently held stream normally, as if the provider finished."""
    global stream_release
    released, stream_release = stream_release, asyncio.Event()
    released.set()
    return {"released": True}


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


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str] = Field(min_length=1)


@app.post("/v1/embeddings")
async def embed(request: EmbeddingRequest):
    """Fixed four-dimension vectors at one token per input, for soak batch items."""
    if request.model != "fixed-embedding":
        raise HTTPException(400, "This fixture supports only the fixed embedding workload")
    inputs = [request.input] if isinstance(request.input, str) else request.input
    if len(inputs) > 2048:
        raise HTTPException(400, "Too many embedding inputs")
    return {
        "object": "list",
        "model": request.model,
        "data": [
            {"object": "embedding", "index": index, "embedding": [0.5, 0.25, 0.125, 0.0625]}
            for index in range(len(inputs))
        ],
        "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
    }


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
        release = stream_release

        async def chunks():
            chunk = {
                "id": "fixed-stream",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": request.model,
                "choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": None}],
            }
            yield "data: " + json.dumps(chunk) + "\n\n"
            # Only the named functional check gets a short, complete stream.
            # Lifecycle checks retain their long stream and cancellation behavior.
            try:
                delay = (
                    0.01
                    if request.messages
                    == [{"role": "user", "content": "Native complete stream fixture."}]
                    else 300
                )
                try:
                    await asyncio.wait_for(release.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
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
