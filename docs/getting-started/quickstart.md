# Send your first request

Send one test request after DeltaLLM is running and a model is available.

The commands use `http://localhost:4002`, the address for the Docker setup. If you used the
[development setup](installation.md), replace it with `http://localhost:8000`.

This one local test uses the master key. Do not put the master key in an application.

## 1. Find your model name

```bash
curl http://localhost:4002/v1/models \
  -H "Authorization: Bearer YOUR_MASTER_KEY"
```

Replace `YOUR_MASTER_KEY` with the `DELTALLM_MASTER_KEY` value from `.env`.

The response lists the public names your requests can use. The Docker starter model is named
`gpt-4o-mini`. If you chose a different public name, use that name in the next command.

## 2. Send a chat request

```bash
curl -X POST http://localhost:4002/v1/chat/completions \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

Replace `gpt-4o-mini` if your model has a different public name.

A successful response contains the model's answer in `choices[0].message.content`.

## Next step

[Create an application key](first-api-key.md) before connecting application code. An application
key can be limited or revoked without affecting the rest of DeltaLLM.
