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
   uv run prisma migrate deploy --schema=./prisma/schema.prisma
   ```

   If you are changing the Prisma schema itself, use `uv run prisma migrate dev` to create a migration rather than papering over failures with `db push`.

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

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_specific.py
```

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
