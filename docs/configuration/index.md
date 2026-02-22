# Configuration Reference

DeltaLLM is configured through a YAML file (`config.yaml` by default). The config file has four main sections:

| Section | Purpose |
|---------|---------|
| [`model_list`](models.md) | Define LLM model deployments and providers |
| [`router_settings`](router.md) | Configure routing strategy, retries, and timeouts |
| [`general_settings`](general.md) | Authentication, database, Redis, and platform settings |
| `deltallm_settings` | Callbacks, guardrails, and logging |

## Config File Location

By default, DeltaLLM looks for `config.yaml` in the working directory. Override with the `DELTALLM_CONFIG_PATH` environment variable:

```bash
export DELTALLM_CONFIG_PATH=/etc/deltallm/config.yaml
```

## Environment Variable Interpolation

Reference environment variables in any config value using the `os.environ/` prefix:

```yaml
general_settings:
  master_key: os.environ/DELTALLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
```

This keeps secrets out of the config file.

## Minimal Configuration

```yaml
model_list:
  - model_name: gpt-4o-mini
    deltallm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY

general_settings:
  master_key: os.environ/DELTALLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
```

## Full Example

See [`config.example.yaml`](https://github.com/your-org/deltallm/blob/main/config.example.yaml) for a complete annotated example.

## Backward Compatibility

Config files using the legacy `litellm_params` and `litellm_settings` keys are still accepted. DeltaLLM automatically maps them to their `deltallm_*` equivalents.
