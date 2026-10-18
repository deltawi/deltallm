"""Runs one compatibility check with the official OpenAI SDK against a local gateway.

It runs in an isolated environment (tests/openai_sdk/requirements.txt, or the
newest SDK release in the nightly and release runs), so it imports nothing from
the gateway. Each check fails with an assertion when the SDK cannot send the
request or cannot parse the gateway's answer into its own types.
"""

import argparse
import io
import json
import os
import time

import openai
from openai import OpenAI
from pydantic import BaseModel

CHAT_MODEL = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-small"
SPEECH_MODEL = "tts-1"
TRANSCRIPTION_MODEL = "whisper-1"
MESSAGES = [{"role": "user", "content": "Say hello"}]


class Answer(BaseModel):
    city: str
    population: int


def check_models(client: OpenAI) -> None:
    ids = {model.id for model in client.models.list()}
    assert {CHAT_MODEL, EMBEDDING_MODEL, SPEECH_MODEL, TRANSCRIPTION_MODEL} <= ids, ids


def check_chat(client: OpenAI) -> None:
    completion = client.chat.completions.create(model=CHAT_MODEL, messages=MESSAGES)
    assert completion.object == "chat.completion"
    assert completion.choices[0].message.content == "Hello from upstream"
    assert completion.choices[0].finish_reason == "stop"
    assert completion.usage is not None and completion.usage.total_tokens == 12

    raw = client.chat.completions.with_raw_response.create(model=CHAT_MODEL, messages=MESSAGES)
    assert raw.headers.get("x-request-id") or raw.headers.get("x-deltallm-request-id"), dict(
        raw.headers
    )
    assert raw.parse().choices[0].message.content == "Hello from upstream"


def check_chat_stream(client: OpenAI) -> None:
    stream = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=MESSAGES,
        stream=True,
        stream_options={"include_usage": True},
    )
    text, usage, finish = "", None, None
    for chunk in stream:
        assert chunk.object == "chat.completion.chunk"
        for choice in chunk.choices:
            text += choice.delta.content or ""
            finish = choice.finish_reason or finish
        usage = chunk.usage or usage
    assert text == "Hello from upstream", text
    assert finish == "stop"
    assert usage is not None and usage.total_tokens == 12

    with client.chat.completions.stream(model=CHAT_MODEL, messages=MESSAGES) as events:
        final = events.get_final_completion()
    assert final.choices[0].message.content == "Hello from upstream"


def check_chat_tools(client: OpenAI) -> None:
    completion = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[{"role": "user", "content": "Weather in Paris?"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        tool_choice="auto",
    )
    choice = completion.choices[0]
    assert choice.finish_reason == "tool_calls"
    call = choice.message.tool_calls[0]
    assert call.type == "function" and call.function.name == "get_weather"
    assert json.loads(call.function.arguments) == {"city": "Paris"}


def check_chat_structured(client: OpenAI) -> None:
    completion = client.chat.completions.create(
        model=CHAT_MODEL, messages=MESSAGES, response_format={"type": "json_object"}
    )
    assert json.loads(completion.choices[0].message.content) == {
        "city": "Paris",
        "population": 2100000,
    }

    parsed = client.chat.completions.parse(
        model=CHAT_MODEL, messages=MESSAGES, response_format=Answer
    )
    assert parsed.choices[0].message.parsed == Answer(city="Paris", population=2100000)


def check_embeddings(client: OpenAI) -> None:
    # The SDK asks for base64 by default and decodes it back to floats.
    default = client.embeddings.create(model=EMBEDDING_MODEL, input="hello")
    assert default.object == "list" and default.model
    assert default.data[0].embedding == [0.25, -0.5, 0.75]
    assert default.usage.prompt_tokens == 2 and default.usage.total_tokens == 2

    floats = client.embeddings.create(
        model=EMBEDDING_MODEL, input=["one", "two"], encoding_format="float"
    )
    assert [item.index for item in floats.data] == [0, 1]
    assert all(item.embedding == [0.25, -0.5, 0.75] for item in floats.data)


def check_audio_speech(client: OpenAI) -> None:
    speech = client.audio.speech.create(
        model=SPEECH_MODEL, voice="alloy", input="Hello", response_format="mp3"
    )
    assert speech.content == b"ID3-fake-mp3-audio"

    with client.audio.speech.with_streaming_response.create(
        model=SPEECH_MODEL, voice="alloy", input="Hello", response_format="wav"
    ) as response:
        assert response.headers["content-type"].startswith("audio/wav")
        assert b"".join(response.iter_bytes()).startswith(b"RIFF")


def check_audio_transcription(client: OpenAI) -> None:
    def audio() -> tuple[str, io.BytesIO, str]:
        return ("hello.wav", io.BytesIO(b"RIFF-fake-wav-audio"), "audio/wav")

    result = client.audio.transcriptions.create(model=TRANSCRIPTION_MODEL, file=audio())
    assert result.text == "hello world"

    text = client.audio.transcriptions.create(
        model=TRANSCRIPTION_MODEL, file=audio(), response_format="text"
    )
    assert isinstance(text, str) and text.strip() == "hello world", text

    verbose = client.audio.transcriptions.create(
        model=TRANSCRIPTION_MODEL, file=audio(), response_format="verbose_json"
    )
    assert verbose.text == "hello world" and verbose.language == "english"
    assert verbose.duration == 1.0


def _upload_batch_input(client: OpenAI, count: int) -> str:
    lines = [
        {
            "custom_id": f"request-{index}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {"model": CHAT_MODEL, "messages": MESSAGES, "max_tokens": 16},
        }
        for index in range(count)
    ]
    data = "\n".join(json.dumps(line) for line in lines).encode()
    uploaded = client.files.create(file=("input.jsonl", data), purpose="batch")
    assert uploaded.object == "file" and uploaded.purpose == "batch"
    assert uploaded.bytes == len(data)
    assert client.files.retrieve(uploaded.id).id == uploaded.id
    assert client.files.content(uploaded.id).content == data
    return uploaded.id


def check_batch(client: OpenAI) -> None:
    input_file_id = _upload_batch_input(client, 2)
    batch = client.batches.create(
        input_file_id=input_file_id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
        metadata={"suite": "openai-sdk"},
    )
    assert batch.object == "batch" and batch.input_file_id == input_file_id
    assert batch.metadata == {"suite": "openai-sdk"}

    deadline = time.monotonic() + 30
    while batch.status not in {"completed", "failed", "expired", "cancelled"}:
        assert time.monotonic() < deadline, batch
        time.sleep(0.1)
        batch = client.batches.retrieve(batch.id)
    assert batch.status == "completed", batch
    assert batch.request_counts.total == 2 and batch.request_counts.completed == 2

    output = client.files.content(batch.output_file_id).text
    rows = [json.loads(line) for line in output.splitlines()]
    assert sorted(row["custom_id"] for row in rows) == ["request-0", "request-1"]
    for row in rows:
        assert row["error"] is None and row["response"]["status_code"] == 200
        body = openai.types.chat.ChatCompletion.model_validate(row["response"]["body"])
        assert body.choices[0].message.content == "Hello from upstream"

    listed = client.batches.list(limit=10)
    assert batch.id in {item.id for item in listed.data}


def check_batch_cancel(client: OpenAI) -> None:
    batch = client.batches.create(
        input_file_id=_upload_batch_input(client, 1),
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    cancelled = client.batches.cancel(batch.id)
    assert cancelled.id == batch.id and cancelled.status in {"cancelling", "cancelled"}


def check_errors(client: OpenAI, base_url: str) -> None:
    anonymous = OpenAI(base_url=base_url + "/v1", api_key="sk-not-a-real-key", max_retries=0)
    try:
        anonymous.chat.completions.create(model=CHAT_MODEL, messages=MESSAGES)
    except openai.AuthenticationError as exc:
        assert exc.status_code == 401 and exc.message
    else:
        raise AssertionError("an unknown key was accepted")

    try:
        client.chat.completions.create(model="model-that-does-not-exist", messages=MESSAGES)
    except openai.APIStatusError as exc:
        assert exc.status_code in {400, 403, 404}, exc.status_code
        assert isinstance(exc.body, dict) and exc.body.get("message"), exc.body
    else:
        raise AssertionError("an unknown model was accepted")


CHECKS = {
    "models": check_models,
    "chat": check_chat,
    "chat_stream": check_chat_stream,
    "chat_tools": check_chat_tools,
    "chat_structured": check_chat_structured,
    "embeddings": check_embeddings,
    "audio_speech": check_audio_speech,
    "audio_transcription": check_audio_transcription,
    "batch": check_batch,
    "batch_cancel": check_batch_cancel,
    "errors": check_errors,
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--check", choices=sorted(CHECKS), required=True)
    args = parser.parse_args()
    sdk = OpenAI(
        base_url=args.base_url + "/v1",
        api_key=os.environ["DELTALLM_SDK_TEST_KEY"],
        max_retries=0,
        timeout=20,
    )
    check = CHECKS[args.check]
    if args.check == "errors":
        check(sdk, args.base_url)
    else:
        check(sdk)
    print(json.dumps({"check": args.check, "openai": openai.__version__}))
