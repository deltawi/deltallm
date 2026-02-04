<div align="center">

<!-- Placeholder: Replace with actual logo.svg or logo.png -->
<!-- ![DeltaLLM Logo](assets/logo.png) -->
<h1>🚀 DeltaLLM</h1>

**The open-source LLM gateway for developers. One API. 8+ providers. Enterprise-ready.**

[![Tests](https://github.com/mehditantaoui/deltallm/actions/workflows/ci.yml/badge.svg)](https://github.com/mehditantaoui/deltallm/actions)
[![Coverage](https://codecov.io/gh/mehditantaoui/deltallm/branch/main/graph/badge.svg)](https://codecov.io/gh/mehditantaoui/deltallm)
[![PyPI](https://img.shields.io/pypi/v/deltallm.svg)](https://pypi.org/project/deltallm/)
[![Python](https://img.shields.io/pypi/pyversions/deltallm.svg)](https://pypi.org/project/deltallm/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-blue?logo=docker)](https://github.com/mehditantaoui/deltallm/pkgs/container/deltallm)

[📖 Documentation](https://deltallm.readthedocs.io) • [🚀 Quick Start](#quick-start) • [💻 API Reference](https://deltallm.readthedocs.io/api/) • [🤝 Contributing](CONTRIBUTING.md)

</div>

---

<!-- Placeholder: Replace with actual demo GIF -->
<!-- 
## 🎥 Demo

<p align="center">
  <img src="assets/demo-dashboard.gif" alt="DeltaLLM Dashboard Demo" width="80%">
  <br>
  <i>DeltaLLM admin dashboard - manage organizations, teams, and API keys</i>
</p>
-->

## ✨ Features

### 🔌 **Universal Provider Support**
Access 100+ models across 8+ providers with a single, OpenAI-compatible API:

| Provider | Chat | Embeddings | Streaming | Vision | Tools |
|:---------|:----:|:----------:|:---------:|:------:|:-----:|
| OpenAI (GPT-4, GPT-3.5) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Anthropic (Claude 3.5) | ✅ | ❌ | ✅ | ✅ | ✅ |
| Azure OpenAI | ✅ | ✅ | ✅ | ✅ | ✅ |
| AWS Bedrock | ✅ | ✅ | ✅ | ✅ | ✅ |
| Google Gemini | ✅ | ✅ | ✅ | ✅ | ❌ |
| Cohere | ✅ | ✅ | ✅ | ❌ | ❌ |
| Mistral AI | ✅ | ✅ | ✅ | ❌ | ✅ |
| Groq | ✅ | ❌ | ✅ | ❌ | ✅ |

### ⚡ **Smart Routing & Reliability**
- **5 Load Balancing Strategies** - Round-robin, weighted, least-busy, latency-based, cost-based
- **Automatic Retries** - Exponential backoff with jitter
- **Intelligent Fallbacks** - Automatic failover between providers
- **Health Tracking** - Automatic cooldown for unhealthy endpoints

### 🏢 **Enterprise Features**
- **RBAC** - 7 roles from superuser to team member with granular permissions
- **Organizations & Teams** - Hierarchical access control
- **Budget Enforcement** - Set spending limits at org, team, and key levels
- **Audit Logging** - Track every API call and admin action
- **Guardrails** - PII detection, toxic content filtering, prompt injection detection

### 💻 **Beautiful Admin Dashboard**

<!-- Placeholder: Replace with actual dashboard screenshot -->
<!--
<p align="center">
  <img src="assets/screenshot-dashboard-overview.png" alt="Dashboard" width="90%">
</p>
-->

Built with React + TypeScript + Tailwind CSS:
- 📊 Real-time usage analytics
- 👥 Organization & team management  
- 🔑 API key management with granular permissions
- 💰 Budget tracking and alerts
- 📜 Audit logs with filtering
- 🛡️ Guardrails configuration

### 💰 **Cost Optimization**
- **Token Caching** - In-memory LRU + Redis backends
- **Cost Tracking** - Detailed spend logs for every request
- **Budget Alerts** - Get notified before hitting limits

---

## 🚀 Quick Start

### Deploy DeltaLLM (Server + UI)

The fastest way to get started:

```bash
# Clone the repository
git clone https://github.com/mehditantaoui/deltallm.git
cd deltallm

# Start both server and UI with Docker Compose
docker-compose up -d

# Or start manually:
# Terminal 1: Start the server
pip install deltallm
deltallm server --port 8000

# Terminal 2: Start the admin dashboard
cd admin-dashboard && npm install && npm run dev
```


**Access points:**

- 🖥️ **Admin Dashboard**: http://localhost:5173
- ⚡ **API Server**: http://localhost:8000
- 📚 **API Docs**: http://localhost:8000/docs

### UI Demo

<p align="center">
  <!-- TODO: Replace with actual demo GIF -->
  <img src="assets/demo-ui.gif" alt="DeltaLLM Admin Dashboard Demo" width="90%">
  <br>
  <i>DeltaLLM Admin Dashboard - Manage organizations, teams, and API keys</i>
</p>

### 30-Second Example

API keys are **optional** for getting started:

```python
import asyncio
from deltallm import completion

async def main():
    # Works with ANY provider - just change the model name
    response = await completion(
        model="gpt-4o",  # or "claude-3-sonnet", "gemini-1.5-pro", etc.
        messages=[{"role": "user", "content": "Hello, DeltaLLM!"}],
        # api_key="optional"  # Optional: only if you have API keys configured
    )
    print(response.choices[0].message.content)

asyncio.run(main())
```

### Start the Proxy Server

```bash
# Run with Docker (quickest) - no API keys required to start
docker run -p 8000:8000 \
  ghcr.io/mehditantaoui/deltallm:latest

# With provider API keys (optional)
docker run -p 8000:8000 \
  -e OPENAI_API_KEY="sk-..." \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  ghcr.io/mehditantaoui/deltallm:latest

# Or install locally
pip install deltallm
deltallm server --port 8000
```

Use with any OpenAI-compatible client:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000",
    api_key="any-key"  # Your DeltaLLM key (optional for testing)
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

---

## 📚 Documentation

- **[Quick Start Guide](docs/quickstart.md)** - Get running in 5 minutes
- **[Provider Configuration](docs/providers/)** - Set up OpenAI, Anthropic, Azure, and more
- **[Deployment Guide](docs/deployment/)** - Docker, Kubernetes, cloud deployment
- **[Enterprise Features](docs/enterprise/)** - RBAC, budgets, audit logs
- **[API Reference](docs/api/)** - Full API documentation

---

## 🏗️ Architecture

<!-- Placeholder: Replace with actual architecture diagram -->
<!--
<p align="center">
  <img src="assets/architecture.svg" alt="DeltaLLM Architecture" width="80%">
</p>
-->

```
┌─────────────────────────────────────────────────────────────────┐
│                         Clients                                  │
│  (OpenAI SDK, LangChain, LlamaIndex, Custom Apps)              │
└────────────────────────────────────┬────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DeltaLLM Gateway                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │   Auth &    │  │   Rate      │  │      Guardrails         │ │
│  │  API Keys   │  │  Limiting   │  │  PII | Toxic | Injection│ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                 Smart Router                             │   │
│  │   Load Balancing │ Fallbacks │ Retries │ Cooldowns      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Provider Adapters                           │   │
│  │  OpenAI │ Anthropic │ Azure │ Bedrock │ Gemini │ ...    │   │
│  └─────────────────────────────────────────────────────────┘   │
└────────────────────────────────────┬────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Data Layer                               │
│   PostgreSQL (users, keys, logs)  │  Redis (cache, sessions)   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 💻 Code Examples

### Multi-Provider in One Request

```python
import asyncio
from deltallm import completion

async def compare_providers():
    prompt = "Explain quantum computing in one sentence"
    
    # Same code, different providers
    providers = [
        ("gpt-4o", "openai"),
        ("claude-3-sonnet", "anthropic"),
        ("gemini-1.5-pro", "gemini"),
    ]
    
    for model, provider in providers:
        response = await completion(
            model=f"{provider}/{model}" if provider != "openai" else model,
            messages=[{"role": "user", "content": prompt}],
            api_key=get_api_key(provider)
        )
        print(f"{provider}: {response.choices[0].message.content}\n")

asyncio.run(compare_providers())
```

### Streaming Responses

```python
import asyncio
from deltallm import completion

async def stream_example():
    response = await completion(
        model="claude-3-sonnet",
        messages=[{"role": "user", "content": "Tell me a story"}],
        api_key="sk-ant-...",
        stream=True
    )
    
    async for chunk in response:
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)

asyncio.run(stream_example())
```

### Using Embeddings

```python
import asyncio
from deltallm import embedding

async def embed_texts():
    response = await embedding(
        model="text-embedding-3-small",
        input=["Hello world", "Goodbye world"],
        api_key="sk-..."
    )
    
    for item in response.data:
        print(f"Embedding {item.index}: {len(item.embedding)} dimensions")

asyncio.run(embed_texts())
```

### Configuration File

```yaml
# config.yaml
model_list:
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: ${OPENAI_API_KEY}
      rpm: 1000
  
  - model_name: claude-3-sonnet
    litellm_params:
      model: anthropic/claude-3-5-sonnet-20241022
      api_key: ${ANTHROPIC_API_KEY}
      fallback: [gpt-4o]

router_settings:
  routing_strategy: "least-busy"
  num_retries: 3
  timeout: 30

server:
  port: 8000
  host: "0.0.0.0"
```

---

## 🐳 Docker Deployment

```bash
# Quick start
docker run -p 8000:8000 \
  -e OPENAI_API_KEY="sk-..." \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  ghcr.io/mehditantaoui/deltallm:latest

# With custom config
docker run -p 8000:8000 \
  -v $(pwd)/config.yaml:/app/config.yaml \
  ghcr.io/mehditantaoui/deltallm:latest \
  deltallm server --config /app/config.yaml

# Full stack with PostgreSQL and Redis
docker-compose up -d
```

---

## 🛠️ Development

```bash
# Clone the repository
git clone https://github.com/mehditantaoui/deltallm.git
cd deltallm

# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=deltallm --cov-report=html

# Start the server
deltallm server --port 8000

# Start the dashboard
cd admin-dashboard && npm install && npm run dev
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed contribution guidelines.

---

## 📊 Project Stats

| Metric | Value |
|--------|-------|
| **Providers** | 8 |
| **Supported Models** | 100+ |
| **API Endpoints** | 48 |
| **Test Coverage** | 70%+ |
| **Tests** | 356+ |
| **Lines of Code** | ~15,000 |
| **License** | MIT |

---

## 🤝 Why DeltaLLM vs Alternatives?

| Feature | DeltaLLM | LiteLLM | Custom Code |
|---------|----------|---------|-------------|
| Open Source | ✅ MIT | ⚠️ GPL/BUSL | ✅ |
| Multi-provider | ✅ | ✅ | ❌ |
| Enterprise Features | ✅ | ✅ | ❌ |
| Admin Dashboard | ✅ | ✅ | ❌ |
| Self-hosted | ✅ | ✅ | ✅ |
| Cost | Free | Free/Paid | Dev time |

**DeltaLLM is**: Open source (MIT), self-hosted, developer-focused, with no vendor lock-in.

---

## 🗺️ Roadmap

- [x] 8 provider support
- [x] RBAC and organizations
- [x] Budget enforcement
- [x] Admin dashboard
- [x] Guardrails
- [ ] Additional providers (Together AI, Perplexity, AI21)
- [ ] Semantic caching
- [ ] Prompt management
- [ ] A/B testing
- [ ] Fine-tuning support
- [ ] Kubernetes operator

---

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for:

- Development setup
- Code style guidelines
- Adding new providers
- Submitting pull requests

## 📄 License

DeltaLLM is released under the [MIT License](LICENSE).

## 🙏 Acknowledgments

- Inspired by [LiteLLM](https://github.com/BerriAI/litellm)
- Built with [FastAPI](https://fastapi.tiangolo.com/), [Pydantic](https://docs.pydantic.dev/), and [React](https://react.dev/)

---

<div align="center">

**[⭐ Star us on GitHub](https://github.com/mehditantaoui/deltallm)** — It means a lot!

Made with ❤️ by the DeltaLLM team

</div>
