# Compatible chat protocol fixtures

These are deterministic synthetic examples shaped from the official API references:
[DeepSeek](https://api-docs.deepseek.com/api/create-chat-completion/),
[Z.ai](https://docs.z.ai/api-reference/llm/chat-completion),
[Qwen](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope),
[Tencent](https://www-sg.tencentcloud.com/ind/document/product/1300/80632), and
[MiniMax](https://platform.minimax.io/docs/api-reference/text-openai-api).
They contain no live provider responses or credentials. Model IDs and endpoint
bases were checked on September 9, 2026.

Each JSON file includes text success, a null-content tool completion, and SSE
frames for role, reasoning, text, usage, and termination. Token counts are small
test values. DeepSeek uses its native cache-hit counter; other examples exercise
OpenAI-style cache details. MiniMax reasoning blocks include opaque fields to
verify lossless tool-turn round trips.

HTTP failures, malformed frames, timeouts, cancellation, discovery bounds,
authorization, failover, and runtime reload use separate deterministic tests in
`tests/providers/`. Passing fixtures does not certify a live account's model
entitlements, regional configuration, or changing provider behavior.
