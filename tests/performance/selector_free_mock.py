"""Fixed local one-token provider for the PR 2 selector-free regression profile."""

from time import perf_counter

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
calls = 0
duration_seconds = 0.0


class MockRequest(BaseModel):
    model: str


@app.post("/v1/chat/completions")
async def completion(request: MockRequest) -> dict[str, object]:
    global calls, duration_seconds
    started = perf_counter()
    calls += 1
    result = {
        "id": "local-fixed",
        "object": "chat.completion",
        "created": 1,
        "model": request.model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
    }
    duration_seconds += perf_counter() - started
    return result


@app.get("/stats")
async def stats() -> dict[str, object]:
    return {"calls": calls, "handler_seconds": duration_seconds}
