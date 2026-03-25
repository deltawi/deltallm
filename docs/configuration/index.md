# Configuration Reference

DeltaLLM reads a YAML config file, usually `config.yaml`. For most teams, this file should stay small: connection settings, auth settings, and only the defaults you want to manage centrally.

## Quick Path

If you want the simplest runtime model:

1. Keep secrets in environment variables
2. Point DeltaLLM at Postgres and Redis
3. Use `db_only` model deployment mode
4. Manage models from the Admin UI after startup

```yaml
general_settings:
  master_key: os.environ/DELTALLM_MASTER_KEY
  salt_key: os.environ/DELTALLM_SALT_KEY
  database_url: os.environ/DATABASE_URL
  redis_url: os.environ/REDIS_URL
  model_deployment_source: db_only
  model_deployment_bootstrap_from_config: true
```

For the starter file used by the getting-started guides, copy:

```bash
cp config.example.yaml config.yaml
```

`config.example.yaml` is intentionally curated rather than exhaustive:

- active values cover the minimum local/dev setup
- secrets come from environment variables
- advanced blocks stay commented with guidance on when to enable them

## Main Sections

| Section | Purpose |
| --- | --- |
| [`model_list`](models.md) | File-based model definitions and bootstrap data |
| [`router_settings`](router.md) | Routing, retry, timeout, and alias behavior |
| [`general_settings`](general.md) | Auth, database, Redis, cache, health, sessions, and platform defaults |
| `deltallm_settings` | Fallbacks, callbacks, guardrails, and logging behavior |

## Config File Location

By default, DeltaLLM reads `config.yaml` from the working directory. Override that with:

```bash
export DELTALLM_CONFIG_PATH=/etc/deltallm/config.yaml
```

## Environment Variable Interpolation

Any config value can reference an environment variable with the `os.environ/` prefix:

```yaml
general_settings:
  master_key: os.environ/DELTALLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
```

This is the recommended way to handle secrets.

## Starter Example

See [`config.example.yaml`](https://github.com/deltawi/deltallm/blob/main/config.example.yaml) for the maintained starter config used by the docs.

## Compatibility Note

Legacy `litellm_params` and `litellm_settings` keys are still accepted. DeltaLLM maps them to the `deltallm_*` names automatically.
