# Anthropic Compatibility Upgrade TODO

## Goal

Upgrade provider compatibility with minimal code noise while preserving shared request lifecycle behavior.

## Checklist

- [x] Analyze existing provider routing + stream path
- [x] Identify OpenAI compatibility regression root cause (`tool_choice` without `tools`)
- [x] Add explicit Anthropic compatibility plan in code/docs tracker
- [x] Refactor stream path to use provider adapter stream translation
- [x] Implement Anthropic SSE -> canonical chat chunk translation
- [x] Remove Anthropic streaming fail-fast guard once translator is in place
- [x] Add focused tests for OpenAI + Anthropic adapter compatibility
- [x] Run targeted tests and capture outcomes

## Scope Notes

- OpenAI / Groq / vLLM stay on OpenAI-compatible adapter path.
- Anthropic uses native `/v1/messages` request/response mapping.
- Streaming cache behavior remains unchanged unless validated safe for provider stream semantics.

## Validation Notes (2026-02-27)

- Direct provider verification:
  - OpenAI direct `chat/completions` succeeds with provided key.
  - Anthropic direct `/v1/messages` with `claude-sonnet-4-6` succeeds.
- Anthropic gateway E2E using scoped key and model `anthropic-sonnet-anthok1772218123`:
  - `POST /v1/chat/completions` -> `200`
  - `POST /v1/completions` -> `200`
  - `POST /v1/responses` -> `200`
  - Stream output translated to OpenAI-style SSE chunks + `[DONE]`.
  - `/ui/api/logs` captured usage entries for the E2E team (`LOG_COUNT=3`, `TOKENS=90`).
- Focused automated tests:
  - `uv run pytest -q tests/test_provider_compat.py tests/test_text_endpoints.py`
  - Result: `9 passed`
