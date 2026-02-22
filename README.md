# DeltaLLM Core API (Phase 1)

FastAPI proxy implementing OpenAI-compatible endpoints:
- `POST /v1/chat/completions`
- `POST /v1/embeddings`
- `GET /v1/models`
- `GET /health/liveliness`
- `GET /health/readiness`

## Run

```bash
pip install -r requirements.txt
uvicorn src.main:app --reload
```

## Config

Set environment variables or provide `config.yaml` (see `config.example.yaml`).

Important variables:
- `DELTALLM_CONFIG_PATH`
- `DELTALLM_OPENAI_API_KEY`
- `DELTALLM_DATABASE_URL`
- `DELTALLM_REDIS_URL`
- `DELTALLM_SALT_KEY`

## Test

```bash
pytest tests/
```
