# DeltaLLM

**An open-source LLM gateway that provides a unified API for multiple LLM providers with enterprise-grade features.**

DeltaLLM acts as a proxy between your applications and LLM providers like OpenAI, Anthropic, Azure OpenAI, Groq, and more. It gives you a single OpenAI-compatible API while adding powerful features on top.

---

## Key Features

- **Unified API** — One OpenAI-compatible endpoint for 100+ LLM providers and models
- **Virtual API Keys** — Issue scoped keys with budgets, rate limits, and model restrictions
- **Routing & Failover** — Multiple routing strategies with automatic failover and retries
- **Guardrails** — Built-in PII detection and prompt injection protection
- **Spend Tracking** — Per-key, per-team, and per-model cost attribution
- **Rate Limiting** — Hierarchical limits at organization, team, user, and key levels
- **Caching** — Response caching with memory, Redis, or S3 backends
- **RBAC** — Role-based access control with platform, organization, and team scopes
- **Authentication** — Session-based login, master key, SSO (Microsoft Entra, Google, Okta, OIDC)
- **Admin Dashboard** — Full-featured web UI for managing the gateway
- **Observability** — Prometheus metrics, request logging, and spend analytics

## How It Works

```
┌──────────────┐     ┌──────────────────────────────────┐     ┌──────────────┐
│              │     │           DeltaLLM                │     │   OpenAI     │
│  Your App    │────▶│  Auth → Rate Limit → Guardrails   │────▶│   Anthropic  │
│  (OpenAI SDK)│◀────│  Route → Cache → Provider Call    │◀────│   Azure      │
│              │     │  Spend Track → Callbacks           │     │   Groq ...   │
└──────────────┘     └──────────────────────────────────┘     └──────────────┘
```

Your applications use the standard OpenAI SDK — just change the `base_url` to point at DeltaLLM. The gateway handles authentication, routing, reliability, and cost tracking transparently.

## Quick Links

- [Docker Compose](getting-started/docker.md) — Fastest way to run DeltaLLM locally
- [Installation](getting-started/installation.md) — Full local setup for development and contribution
- [Quick Start](getting-started/quickstart.md) — Use the gateway with curl, Python, and JavaScript
- [Configuration Reference](configuration/index.md) — Full config.yaml documentation
- [API Reference](api/index.md) — OpenAI-compatible and admin API endpoints
- [Admin UI Guide](admin-ui/index.md) — Managing the gateway through the web dashboard
