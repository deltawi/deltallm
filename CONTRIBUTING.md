# Contributing to DeltaLLM

Thank you for your interest in contributing to DeltaLLM! This document provides guidelines for contributing to the project.

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL 15+
- Redis 7+ (optional but recommended)
- [uv](https://docs.astral.sh/uv/) for Python dependency management

### Setting Up Your Development Environment

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/deltallm.git
   cd deltallm
   ```

2. **Install Python dependencies**
   ```bash
   uv sync --dev
   ```

3. **Install UI dependencies**
   ```bash
   cd ui
   npm ci
   cd ..
   ```

4. **Set up environment variables**
   ```bash
   export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/deltallm"
   export DELTALLM_CONFIG_PATH=./config.yaml
   export DELTALLM_MASTER_KEY="$(python3 -c 'import secrets; print(\"sk-\" + secrets.token_hex(20) + \"A1\")')"
   export DELTALLM_SALT_KEY="$(openssl rand -hex 32)"
   export OPENAI_API_KEY="sk-your-openai-key"
   ```

5. **Create config and initialize the database**
   ```bash
   cp config.example.yaml config.yaml
   uv run prisma generate --schema=./prisma/schema.prisma
   uv run prisma db push --schema=./prisma/schema.prisma
   ```

6. **Start the development servers**
   
   Backend:
   ```bash
   uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
   ```
   
   UI (in another terminal):
   ```bash
   cd ui
   npm run dev
   ```

See [Local Development](README.md#local-development) in the README for more details.

## Development Workflow

1. **Create a branch** for your changes
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** and ensure they follow our code style

3. **Run tests** to make sure nothing is broken
   ```bash
   uv run pytest
   ```

4. **Run the linter** to check code style
   ```bash
   uv run ruff check .
   ```

5. **Commit your changes** with a clear commit message

6. **Push to your fork** and submit a pull request

## Pull Request Guidelines

- **Describe what your PR does** and why it's needed
- **Reference any related issues** using `Fixes #123` or `Closes #123`
- **Ensure tests pass** before submitting
- **Keep changes focused** — one feature or fix per PR
- **Update documentation** if your changes affect usage

## Code Style

- We use [Ruff](https://docs.astral.sh/ruff/) for Python linting
- Follow PEP 8 style guidelines
- Write docstrings for public functions and classes
- Keep functions focused and modular

## Testing

- Write tests for new features
- Ensure existing tests pass
- Use `pytest` for running tests

Every Python test belongs to exactly one dependency lane. Pytest assigns `app`
automatically when a test uses the shared `test_app` fixture and assigns
`hermetic` when no other lane applies. A test module that opens a real
infrastructure client must declare its lane for every test in that module,
normally with a module-level `pytestmark`.

| Lane | What it exercises | Required dependency |
| --- | --- | --- |
| `hermetic` | Domain, service, repository-fake, and focused component behavior | None outside the Python process |
| `app` | The complete in-process FastAPI route graph with fake adapters and stores | None outside the Python process |
| `postgres` | SQL, constraints, transactions, locking, and Prisma behavior | Migrated PostgreSQL plus the generated Prisma client |
| `redis` | Lua, cross-client coordination, reconnect, TTL, and outage behavior | Real Redis through `DELTALLM_TEST_REDIS_URL` |
| `helm` | Chart schema, rendering, and deployment profiles | Helm CLI and built chart dependencies |

The `integration` marker remains an aggregate selector for the `postgres`,
`redis`, and `helm` lanes. It is not a primary lane.

```bash
# Run all tests
uv run pytest

# Show the current test count in each lane
uv run pytest --collect-only -qq --dependency-lane-report | tail -n 7

# Run one dependency lane
uv run pytest -q -m hermetic
uv run pytest -q -m app

# Run PostgreSQL tests after generating the client and migrating the test database
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/deltallm_test \
  uv run pytest -q -m postgres

# Run Redis integration tests
DELTALLM_TEST_REDIS_URL=redis://localhost:6379/0 \
  REDIS_URL=redis://localhost:6379/0 \
  uv run pytest -q -m redis

# Run Helm tests after building chart dependencies
uv run pytest -q -m helm

# Run specific test file
uv run pytest tests/test_specific.py
```

The collection hook fails when a test declares multiple primary lanes or when
real Prisma, Redis, or Helm usage is missing its matching explicit marker. This
keeps lane selection exhaustive and prevents infrastructure tests from silently
running in a fake-only job.

## Reporting Issues

When reporting bugs, please include:

- **Clear description** of the issue
- **Steps to reproduce** the problem
- **Expected vs actual behavior**
- **Environment details** (OS, Python version, etc.)
- **Relevant logs or error messages**

## Questions?

- Check the [documentation](https://deltallm.readthedocs.io)
- Open a [discussion](https://github.com/deltawi/deltallm/discussions) for questions
- Join our community conversations

## License

By contributing to DeltaLLM, you agree that your contributions will be licensed under the MIT License.

---

Thank you for helping make DeltaLLM better! 🚀
