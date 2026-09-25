# DeltaLLM

DeltaLLM gives your applications one place to call AI models. It connects to providers such as
OpenAI, Anthropic, Gemini, and Bedrock, while your team controls access, routing, spending, safety,
and monitoring.

[Start with Docker](getting-started/docker.md){ .md-button .md-button--primary }
[See how DeltaLLM works](concepts/architecture.md){ .md-button }

## Choose what you want to do

| I want to… | Start here |
| --- | --- |
| Try DeltaLLM on my computer | [Get started](getting-started/index.md) |
| Connect an application to AI models | [Build with DeltaLLM](features/index.md) |
| Manage models, keys, teams, and spending | [Administer DeltaLLM](admin-ui/index.md) |
| Run DeltaLLM in production | [Deploy and operate](deployment/index.md) |
| Understand the main ideas | [Understand the system](concepts/index.md) |
| Look up an API, setting, or provider | [Reference](reference/index.md) |

## The shortest path to a working request

1. [Start DeltaLLM with Docker](getting-started/docker.md).
2. [Add a model](getting-started/first-model.md).
3. [Send a test request](getting-started/quickstart.md).
4. [Create a key for your application](getting-started/first-api-key.md).

You can usually complete these steps in a few minutes if you already have a provider API key.

## What DeltaLLM manages

```text
Your applications             DeltaLLM                    AI services
┌─────────────────┐      ┌─────────────────────┐      ┌─────────────────┐
│ OpenAI SDKs     │─────▶│ Access and limits   │─────▶│ OpenAI          │
│ HTTP clients    │◀─────│ Routing and safety  │◀─────│ Anthropic       │
│ Agent tools     │      │ Cost and monitoring │      │ Gemini and more │
└─────────────────┘      └──────────┬──────────┘      └─────────────────┘
                                    │
                         PostgreSQL and Redis
```

Applications call a public model name. DeltaLLM chooses the right provider deployment, applies
your rules, sends the request, and records the result. Read [What happens to a
request](concepts/request-lifecycle.md) when you need more detail.

## Before production

The Docker quickstart is for learning and testing. A production deployment also needs protected
network access, reliable PostgreSQL and Redis services, backups, monitoring, and a safe upgrade
process. Use the [production checklist](deployment/production-checklist.md) before serving real
traffic.

## Get help

- Report a problem in [GitHub Issues](https://github.com/deltawi/deltallm/issues).
- Ask questions in [GitHub Discussions](https://github.com/deltawi/deltallm/discussions).
- Read the [documentation policy](project/documentation-governance.md) before updating these docs.
