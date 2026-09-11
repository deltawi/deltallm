from datetime import UTC, datetime
import json

import httpx

from src.providers.anthropic import AnthropicAdapter
from src.providers.azure import AzureOpenAIAdapter
from src.providers.bedrock import BedrockAdapter
from src.providers.gemini import GeminiAdapter
from src.providers.openai import OpenAIAdapter
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.profiled_chat import ProfiledChatAdapter
from src.providers.registry import ProviderErrorMapperRegistry
from src.router.selection.contracts import SelectorPrompt
from src.router.selection.provider import ConcreteSelectorTarget, SelectorProviderHop

PROMPT = SelectorPrompt(system="Fixed classifier instruction", user='{"request":"hello"}')


class FixedClock:
    @classmethod
    def now(cls, tz=UTC):
        return datetime(2026, 9, 7, 12, 0, tzinfo=tz)


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, read_error=None, close_error=None, pause=None):
        self.chunks = chunks
        self.read_error, self.close_error, self.pause = read_error, close_error, pause
        self.closed = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.pause is not None:
            await self.pause.wait()
        if self.read_error:
            raise self.read_error

    async def aclose(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error


def registry(client):
    return ProviderErrorMapperRegistry(
        openai=OpenAIAdapter(client),
        azure_openai=AzureOpenAIAdapter(client),
        anthropic=AnthropicAdapter(client),
        gemini=GeminiAdapter(client),
        bedrock=BedrockAdapter(client),
        compatible_chat={
            name: ProfiledChatAdapter(client, profile)
            for name, profile in CHAT_PROVIDER_PROFILES.items()
        },
    )


def bridge(client, provider="openai", **kwargs):
    params = {
        "provider": provider,
        "model": f"{provider}/classifier-small",
        "api_key": "provider-test-key",
        "api_base": "https://provider.test/v1",
        "timeout": 50,
        "aws_access_key_id": "test-access",
        "aws_secret_access_key": "test-secret",
        "aws_session_token": "test-session",
        "region": "us-east-1",
        "default_params": {"tools": [{"secret": "must-not-leak"}], "temperature": 1.8},
    }
    return SelectorProviderHop(
        client=client,
        adapters=registry(client),
        target=ConcreteSelectorTarget.from_config("classifier-concrete", params),
        default_openai_base_url="https://default.test/v1",
        **kwargs,
    )


def response_body(provider="openai", text='{"lane":"economy"}'):
    if provider in ("openai", "azure", "vllm"):
        return {
            "id": "test",
            "created": 1,
            "model": "classifier-small",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
        }
    if provider == "anthropic":
        return {
            "id": "test",
            "type": "message",
            "role": "assistant",
            "model": "classifier-small",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 13, "output_tokens": 5},
        }
    if provider == "gemini":
        return {
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {
                "promptTokenCount": 13,
                "candidatesTokenCount": 5,
                "totalTokenCount": 18,
            },
        }
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 13, "outputTokens": 5, "totalTokens": 18},
    }


def encoded(body):
    return json.dumps(body).encode()
