# OpenAI SDK Compatibility

DeltaLLM tests its proxy endpoints with the official
[OpenAI Python SDK](https://github.com/openai/openai-python). The tests send real SDK calls
to the gateway and check that the SDK can parse every answer into its own types.

## What is tested

| Area | SDK calls |
| --- | --- |
| Models | `models.list` |
| Chat | `chat.completions.create` (plain, streaming with `include_usage`, tools, `json_object`), `chat.completions.stream`, `chat.completions.parse` with a Pydantic schema, `with_raw_response` |
| Embeddings | `embeddings.create` with the SDK default (base64, decoded by the SDK) and with `encoding_format="float"` |
| Audio | `audio.speech.create` (mp3, and wav through `with_streaming_response`), `audio.transcriptions.create` with `json`, `text` and `verbose_json` |
| Files and batches | `files.create`, `files.retrieve`, `files.content`, `batches.create`, `batches.retrieve` until the batch completes, `batches.list`, `batches.cancel`, and parsing each output line as a `ChatCompletion` |
| Errors | an unknown key raises `AuthenticationError`, an unknown model raises `APIStatusError` with the error message |

The gateway runs with its real routes, authentication, routing, batch storage in PostgreSQL
and the real batch worker. Only the provider behind the gateway is a fake OpenAI upstream.

Not covered yet: the Responses API, images, rerank, `/v1/messages` (see
[Anthropic Messages](messages.md)), and Realtime, which has its own SDK test. DeltaLLM does
not implement `audio.translations`, `files.list` or `files.delete`.

## When the tests run

| When | SDK version |
| --- | --- |
| Every pull request and push to `main` (PostgreSQL test lane) | Pinned in `tests/openai_sdk/requirements.txt` |
| Every night | Newest release on PyPI |
| Before a release publishes images | Newest release on PyPI. A failure stops the release. |

Dependabot proposes updates to the pinned version weekly, seven days after an SDK
release is published.

The nightly and release runs use the
[`OpenAI SDK compatibility` workflow](https://github.com/deltawi/deltallm/actions/workflows/openai-sdk-compat.yml).
Start it by hand with a specific version, such as `3.35.1`, to check an older or newer SDK.
When a scheduled run fails, GitHub notifies the person who last changed the workflow's schedule.

## Run the tests locally

The SDK runs in its own virtual environment, so it never mixes with the gateway's dependencies.
You need PostgreSQL with migrations applied, as for the other PostgreSQL tests.

```bash
uv venv /tmp/deltallm-openai-sdk
uv pip sync --python /tmp/deltallm-openai-sdk/bin/python tests/openai_sdk/requirements.txt
# or test the newest SDK instead:
# uv pip install --python /tmp/deltallm-openai-sdk/bin/python --upgrade openai

DELTALLM_OPENAI_SDK_PYTHON=/tmp/deltallm-openai-sdk/bin/python \
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/deltallm_test \
uv run pytest -q -m postgres tests/test_openai_sdk_compat_postgres.py
```

Without `DELTALLM_OPENAI_SDK_PYTHON` the tests are skipped locally and fail in CI.

## Add a check

Add a function to `tests/openai_sdk/sdk_client.py` and register it in `CHECKS`. Add its name
to the parameter list in `tests/test_openai_sdk_compat_postgres.py`. If the gateway calls a
new provider path, answer it in `fake_openai` in the same test file.
