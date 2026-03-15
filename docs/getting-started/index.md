# Getting Started

This section covers everything you need to get DeltaLLM up and running.

!!! tip "Start here if you want the quickest setup"
    Most developers should begin with [Docker Compose](docker.md). It brings up DeltaLLM, PostgreSQL, and Redis together with the fewest local prerequisites.

## Choose Your Setup

| Method | Best For | Time |
|--------|----------|------|
| [Docker Compose](docker.md) | Fastest local setup, evaluation, demos | ~5 min |
| [Installation](installation.md) | Local development, contributing, debugging | ~10 min |
| [Quick Start](quickstart.md) | Using the gateway with curl, Python, and JavaScript | ~5 min |
| [MCP Quick Start](mcp-quickstart.md) | Register an MCP server and test tool execution end to end | ~5 min |

## Prerequisites

- **Docker + Docker Compose v2+** for the quickest path
- **Python 3.11+** for local installation
- **Node.js 20+** for the admin UI
- **PostgreSQL** database for local installation
- **Redis** for rate limiting and caching
- At least one LLM provider API key (OpenAI, Anthropic, Groq, etc.)
