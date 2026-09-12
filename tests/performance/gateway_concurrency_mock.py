"""Fixed one-token provider for disposable local gateway measurements."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI()


class CompletionRequest(BaseModel):
    model: str
    messages: list[dict[str, object]] = Field(max_length=4)
    max_tokens: int
    stream: bool = False


@app.post("/v1/chat/completions")
async def complete(request: CompletionRequest) -> dict[str, object]:
    if request.model != "fixed-one-token" or request.max_tokens != 1 or request.stream:
        raise HTTPException(
            400, "This fixture supports only the fixed one-token nonstream workload"
        )
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
