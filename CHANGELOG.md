# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial open source release

## [0.9.0] - 2025-02-04

### Added
- **Core SDK**: Unified API for 8+ LLM providers
  - OpenAI (GPT-4, GPT-3.5, Embeddings)
  - Anthropic (Claude 3.5, Claude 3, Claude 2)
  - Azure OpenAI (GPT-4, GPT-3.5)
  - AWS Bedrock (Claude, Llama, Mistral, Titan)
  - Google Gemini (1.5 Pro, 1.0 Pro, Ultra)
  - Cohere (Command-R, Command, Embed)
  - Mistral AI (Large, Medium, Small)
  - Groq (Llama 3.1, Mixtral, Gemma)
- **Router**: 5 load balancing strategies (round-robin, weighted, least-busy, latency-based, cost-based)
- **Caching**: In-memory LRU cache with Redis support
- **Proxy Server**: OpenAI-compatible REST API (FastAPI)
- **Enterprise Features**:
  - RBAC with 7 roles (superuser, org_owner, org_admin, org_member, org_viewer, team_admin, team_member)
  - Organizations and Teams management
  - Budget enforcement (org → team → key hierarchy)
  - Audit logging
  - Guardrails & content filtering (PII, toxic content, injection detection)
- **Admin Dashboard**: React/TypeScript frontend (22 components, 8 pages)
- **Testing**: 356+ tests, 70%+ coverage
- **Docker**: Full containerization with docker-compose
- **Documentation**: Comprehensive docs and examples

### Features
- Streaming support (Server-Sent Events)
- Function calling across providers
- Vision/multimodal support
- Token counting with tiktoken
- Cost tracking and spend logs
- Rate limiting (RPM/TPM)
- Health checks and metrics
- Automatic retries with exponential backoff
- Fallback configuration
- Cooldown management for unhealthy deployments

### API Endpoints
- `POST /v1/chat/completions` - Chat completions
- `POST /v1/embeddings` - Text embeddings
- `GET /v1/models` - List available models
- `GET /v1/models/{id}` - Get model info
- Organizations CRUD (10 endpoints)
- Teams CRUD (9 endpoints)
- Budget management (7 endpoints)
- API keys management (5 endpoints)
- Guardrails policies (5 endpoints)
- Health & metrics (4 endpoints)
- Audit logs (3 endpoints)

## [0.8.0] - 2025-01-15

### Added
- AWS Bedrock provider with Claude support
- Azure OpenAI provider
- Google Gemini provider with vision

## [0.7.0] - 2025-01-01

### Added
- Cohere provider
- Mistral AI provider
- Groq provider for fast inference

## [0.6.0] - 2024-12-15

### Added
- Admin dashboard UI
- RBAC implementation
- Organization management

## [0.5.0] - 2024-12-01

### Added
- Budget tracking and enforcement
- Spend logging
- Team management

## [0.4.0] - 2024-11-15

### Added
- Redis caching backend
- Guardrails framework
- PII detection

## [0.3.0] - 2024-11-01

### Added
- Proxy server with FastAPI
- Rate limiting
- API key management

## [0.2.0] - 2024-10-15

### Added
- Router with load balancing
- Retry system
- Fallback configuration
- Health tracking

## [0.1.0] - 2024-10-01

### Added
- Initial release
- Core SDK with OpenAI and Anthropic providers
- Basic completion and embedding functions
- In-memory caching
- Token counting

---

## Release Notes Format

Each release includes:
- **Added**: New features
- **Changed**: Changes in existing functionality
- **Deprecated**: Soon-to-be removed features
- **Removed**: Now removed features
- **Fixed**: Bug fixes
- **Security**: Security improvements

[Unreleased]: https://github.com/mehditantaoui/deltallm/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/mehditantaoui/deltallm/releases/tag/v0.9.0
[0.8.0]: https://github.com/mehditantaoui/deltallm/releases/tag/v0.8.0
[0.7.0]: https://github.com/mehditantaoui/deltallm/releases/tag/v0.7.0
[0.6.0]: https://github.com/mehditantaoui/deltallm/releases/tag/v0.6.0
[0.5.0]: https://github.com/mehditantaoui/deltallm/releases/tag/v0.5.0
[0.4.0]: https://github.com/mehditantaoui/deltallm/releases/tag/v0.4.0
[0.3.0]: https://github.com/mehditantaoui/deltallm/releases/tag/v0.3.0
[0.2.0]: https://github.com/mehditantaoui/deltallm/releases/tag/v0.2.0
[0.1.0]: https://github.com/mehditantaoui/deltallm/releases/tag/v0.1.0
