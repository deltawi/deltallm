"""Deterministic, local-only provider for independent-selector Docker acceptance."""

from collections import Counter
import json

from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI()
counts: Counter[str] = Counter()


class MockRequest(BaseModel):
    model: str
    messages: list[dict[str, object]]
    max_tokens: int | None = None
    stream: bool = False


@app.post("/v1/chat/completions")
async def complete(request: MockRequest):
    role = request.model if request.model in {"tiny", "economy", "quality"} else "unknown"
    counts[role] += 1
    content = "local answer"
    if role == "tiny":
        lane = "quality" if "complex task" in json.dumps(request.messages).lower() else "economy"
        content = json.dumps({"lane": lane})
    body = {
        "id": "local-fixed",
        "object": "chat.completion",
        "created": 1,
        "model": role,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
    }
    if not request.stream:
        return body
    body["object"] = "chat.completion.chunk"
    body["choices"] = [
        {"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": "stop"}
    ]
    return Response(
        "data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n", media_type="text/event-stream"
    )


@app.get("/stats")
async def stats():
    return dict(counts)
