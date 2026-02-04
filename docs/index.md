# DeltaLLM Documentation

Welcome to the DeltaLLM documentation!

## What is DeltaLLM?

DeltaLLM is an **open-source LLM gateway** that provides a unified API for accessing 100+ models across 8+ providers. Built for developers who need:

- 🔌 **Multi-provider access** - Use one API key for OpenAI, Anthropic, Azure, Bedrock, and more
- ⚡ **Smart routing** - Load balancing, fallbacks, and retries built-in
- 🏢 **Enterprise features** - RBAC, budgets, audit logs, and an admin dashboard
- 💰 **Cost control** - Track spending and enforce budgets at org/team/key levels

## Quick Start

### Using the SDK

```bash
pip install deltallm
```

```python
import asyncio
from deltallm import completion

async def main():
    # Works with any provider
    response = await completion(
        model="gpt-4o",  # or "claude-3-sonnet", "gemini-1.5-pro", etc.
        messages=[{"role": "user", "content": "Hello!"}],
        api_key="your-api-key"
    )
    print(response.choices[0].message.content)

asyncio.run(main())
```

### Using the Proxy Server

```bash
# Start the server
deltallm server --port 8000

# Use with any OpenAI-compatible client
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Using Docker

```bash
docker run -p 8000:8000 ghcr.io/mehditantaoui/deltallm:latest
```

## Features

### Providers
| Provider | Chat | Embeddings | Streaming | Vision | Tools |
|----------|------|------------|-----------|--------|-------|
| OpenAI | ✅ | ✅ | ✅ | ✅ | ✅ |
| Anthropic | ✅ | ❌ | ✅ | ✅ | ✅ |
| Azure OpenAI | ✅ | ✅ | ✅ | ✅ | ✅ |
| AWS Bedrock | ✅ | ✅ | ✅ | ✅ | ✅ |
| Google Gemini | ✅ | ✅ | ✅ | ✅ | ❌ |
| Cohere | ✅ | ✅ | ✅ | ❌ | ❌ |
| Mistral AI | ✅ | ✅ | ✅ | ❌ | ✅ |
| Groq | ✅ | ❌ | ✅ | ❌ | ✅ |

### Enterprise Features

- **RBAC** - 7 roles from superuser to team member
- **Organizations & Teams** - Hierarchical access control
- **Budgets** - Enforce spending limits at org/team/key levels
- **Audit Logs** - Track every API call
- **Guardrails** - PII detection and content filtering
- **Admin Dashboard** - React-based management UI

### Reliability Features

- **Load Balancing** - 5 strategies (round-robin, weighted, least-busy, latency, cost)
- **Automatic Retries** - Exponential backoff with jitter
- **Fallbacks** - Automatic failover between providers
- **Caching** - In-memory LRU and Redis backends
- **Rate Limiting** - RPM/TPM limits per key

## Next Steps

- [Quick Start Guide](quickstart.md) - Get up and running in 5 minutes
- [Providers](providers/) - Configure specific providers
- [Deployment](deployment/) - Deploy to production
- [Enterprise Features](enterprise/) - Set up RBAC and budgets
- [API Reference](api/) - Full API documentation

## Getting Help

- [GitHub Issues](https://github.com/mehditantaoui/deltallm/issues) - Bug reports and feature requests
- [GitHub Discussions](https://github.com/mehditantaoui/deltallm/discussions) - Questions and ideas
- Email: everythingjson@gmail.com

## Contributing

We welcome contributions! See [CONTRIBUTING.md](../CONTRIBUTING.md) for details.
