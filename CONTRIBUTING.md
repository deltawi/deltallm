# Contributing to DeltaLLM

First off, thank you for considering contributing to DeltaLLM! 🎉

This document provides guidelines and steps for contributing to the project. We welcome contributions of all kinds - bug fixes, new features, documentation improvements, and more.

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Submitting Changes](#submitting-changes)
- [Adding a New Provider](#adding-a-new-provider)
- [Release Process](#release-process)

## 📜 Code of Conduct

This project and everyone participating in it is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## 🚀 Getting Started

### Ways to Contribute

- **Report bugs**: Open an issue with the bug report template
- **Suggest features**: Open an issue with the feature request template
- **Add provider support**: Request or implement a new LLM provider
- **Improve documentation**: Fix typos, clarify explanations, add examples
- **Submit code**: Fix bugs or implement new features

### Before You Start

1. Check existing issues to avoid duplicates
2. For major changes, open a discussion first
3. Comment on an issue you'd like to work on

## 🛠️ Development Setup

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Node.js 20+ (for dashboard development)
- Docker (optional, for integration tests)

### Quick Setup

```bash
# Clone the repository
git clone https://github.com/mehditantaoui/deltallm.git
cd deltallm

# Install dependencies with uv
uv pip install -e ".[dev]" --system

# Or with pip
pip install -e ".[dev]"

# Run tests to verify setup
pytest
```

### Dashboard Setup

```bash
cd admin-dashboard
npm install
npm run dev
```

### Environment Setup

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your API keys
# Required for testing providers:
# - OPENAI_API_KEY
# - ANTHROPIC_API_KEY
# - etc.
```

## 📝 Making Changes

### Code Style

We use the following tools to maintain code quality:

```bash
# Format code
black .

# Lint code
ruff check .
ruff check . --fix  # Auto-fix issues

# Type checking
mypy deltallm --ignore-missing-imports

# Run all checks
./scripts/lint.sh
```

### Project Structure

```
deltallm/
├── deltallm/               # Main package
│   ├── __init__.py
│   ├── main.py            # Core SDK
│   ├── router.py          # Load balancing router
│   ├── providers/         # Provider adapters
│   ├── cache/             # Caching system
│   ├── budget/            # Budget management
│   ├── guardrails/        # Content filtering
│   ├── rbac/              # Role-based access control
│   └── proxy/             # REST API server
├── admin-dashboard/        # React dashboard
├── tests/                  # Test suite
├── docs/                   # Documentation
├── config/                 # Configuration examples
└── docker/                 # Docker files
```

### Writing Tests

All new code should include tests:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=deltallm --cov-report=html

# Run specific test file
pytest tests/unit/test_router.py -v

# Run integration tests
pytest tests/integration/ -v

# Run with specific marker
pytest -m "not slow"
```

Test guidelines:
- Unit tests go in `tests/unit/`
- Integration tests go in `tests/integration/`
- Provider tests go in `tests/providers/`
- Aim for 80%+ coverage on new code
- Use `pytest-asyncio` for async tests
- Mock external API calls

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add streaming support for Cohere provider
fix: resolve timeout issue in router
docs: update README with new examples
test: add tests for budget enforcement
refactor: simplify provider registry
chore: update dependencies
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `test`: Test changes
- `refactor`: Code refactoring
- `perf`: Performance improvements
- `chore`: Build/dependency changes

## 📤 Submitting Changes

### Pull Request Process

1. **Fork and branch**:
   ```bash
   git checkout -b feat/my-new-feature
   # or
   git checkout -b fix/issue-123
   ```

2. **Make your changes** following our guidelines

3. **Run quality checks**:
   ```bash
   black . && ruff check . && mypy deltallm && pytest
   ```

4. **Commit and push**:
   ```bash
   git add .
   git commit -m "feat: add my new feature"
   git push origin feat/my-new-feature
   ```

5. **Open a Pull Request**:
   - Use the PR template
   - Link related issues
   - Add screenshots for UI changes
   - Ensure CI passes

### PR Review Process

- All PRs require at least one review
- Address review comments promptly
- Keep PRs focused and reasonably sized
- Be respectful and constructive

## 🔌 Adding a New Provider

To add support for a new LLM provider:

### 1. Create Provider File

Create `deltallm/providers/your_provider.py`:

```python
from typing import Any, AsyncIterator

from .base import BaseProvider
from ..types import CompletionRequest, CompletionResponse, StreamChunk


class YourProvider(BaseProvider):
    provider_name = "your_provider"
    supported_endpoints = ["chat", "embeddings"]
    
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform OpenAI format to provider format."""
        return {
            "model": request.model,
            "messages": request.messages,
            # Add provider-specific fields
        }
    
    def transform_response(self, response: dict[str, Any]) -> CompletionResponse:
        """Transform provider response to OpenAI format."""
        return CompletionResponse(
            id=response["id"],
            choices=[...],
            usage=...,
        )
    
    async def chat_completion(
        self,
        request: CompletionRequest,
        api_key: str,
        api_base: Optional[str] = None
    ) -> CompletionResponse:
        """Make chat completion request."""
        # Implementation here
        pass
```

### 2. Register the Provider

Add to `deltallm/providers/registry.py`:

```python
from .your_provider import YourProvider

# In ProviderRegistry.__init__
self.register(YourProvider())
```

### 3. Add Tests

Create `tests/providers/test_your_provider.py`:

```python
import pytest
from deltallm.providers import ProviderRegistry


@pytest.mark.asyncio
async def test_your_provider_chat():
    registry = ProviderRegistry()
    provider = registry.get("your_provider")
    
    # Mock the API call
    # Test request transformation
    # Test response transformation
```

### 4. Add Documentation

Update:
- `README.md` provider table
- `docs/providers/your_provider.md`
- `CHANGELOG.md`

### 5. Test Checklist

- [ ] Request transformation works correctly
- [ ] Response transformation works correctly
- [ ] Streaming is implemented (if supported)
- [ ] Error handling is implemented
- [ ] Unit tests pass
- [ ] Integration tests pass (if API key available)

## 🏷️ Release Process

Maintainers follow this process:

1. Update `CHANGELOG.md`
2. Bump version in `pyproject.toml`
3. Create a git tag: `git tag -a v0.10.0 -m "Release v0.10.0"`
4. Push tag: `git push origin v0.10.0`
5. GitHub Actions will:
   - Run tests
   - Publish to PyPI
   - Build and push Docker images
   - Create GitHub release

## 💬 Getting Help

- Join our [Discord](#) (coming soon)
- Open a [Discussion](https://github.com/mehditantaoui/deltallm/discussions)
- Email: everythingjson@gmail.com

## 🙏 Recognition

Contributors will be:
- Listed in our README
- Mentioned in release notes
- Invited to the organization after significant contributions

---

Thank you for contributing to DeltaLLM! 🚀
