# Get started

This section takes you from an empty machine to your first request through DeltaLLM.

## Recommended path

Most people should use Docker:

1. [Start DeltaLLM with Docker](docker.md).
2. [Check or add your first model](first-model.md).
3. [Send your first request](quickstart.md).
4. [Create an application key](first-api-key.md).

Use the [development setup](installation.md) instead when you plan to change DeltaLLM itself.

## What you need

- Docker with Docker Compose v2 or later
- Git
- An OpenAI API key for the sample setup

You do not need to install PostgreSQL or Redis when you use the Docker setup. Docker Compose starts
them for you.

To start with another provider, follow the Docker setup without the sample model, then
[add a model for your provider](../guides/models-and-providers.md) in the Admin UI.

## Other first steps

- [Connect tools with MCP](mcp-quickstart.md) after normal model requests are working.
- [Choose a production deployment](../deployment/index.md) when you are ready to move beyond local testing.
- [Learn the main concepts](../concepts/index.md) if terms such as deployment, route group, or scoped key are new to you.
