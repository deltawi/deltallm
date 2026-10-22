---
title: Move from LiteLLM
description: Convert a LiteLLM proxy config.yaml into a DeltaLLM configuration and move applications to DeltaLLM.
status: stable
audience: operators
---

# Move from LiteLLM

Use this guide to move a LiteLLM proxy to DeltaLLM. The import tool reads your LiteLLM
`config.yaml` and writes a DeltaLLM `config.yaml`. It also writes a report of each setting that
it changed or could not import.

The tool works offline. It does not connect to LiteLLM, to your providers, or to a database.

## Before you start

You need:

- The LiteLLM `config.yaml` file.
- A new, empty PostgreSQL database for DeltaLLM.
- The environment variables that LiteLLM uses for provider keys, such as `OPENAI_API_KEY`.

!!! warning "Use a new database"
    Do not point DeltaLLM at the LiteLLM database. Both products manage their tables with
    Prisma migrations. One database for the two products is not supported.

## 1. Convert the configuration

From a DeltaLLM source checkout (run `uv run prisma generate --schema=./prisma/schema.prisma`
one time first):

```bash
uv run python -m src.config_import litellm litellm-config.yaml \
  -o config.yaml --report litellm-import-report.md
```

With the DeltaLLM image, mount the folder that contains the file:

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" deltallm/deltallm:<version> \
  python -m src.config_import litellm /work/litellm-config.yaml \
  -o /work/config.yaml --report /work/litellm-import-report.md
```

The tool prints a summary such as:

```text
Imported 8 deployment(s), skipped 2. 6 action(s) needed, 7 setting(s) not imported, 8 changed.
```

Options:

| Option | Effect |
| --- | --- |
| `-o PATH` | The DeltaLLM configuration file to write. Required. |
| `--report PATH` | Also write the report as a Markdown file. |
| `--force` | Replace the output and report files if they exist. |
| `--strict` | Exit with code `2` when a deployment or setting is not imported. Use this in a script. |

Exit codes: `0` when the file is written, `1` when the input cannot be read or converted, and
`2` with `--strict` when something is not imported.

The tool writes the output and the report with owner-only permissions (`0600`). The report
does not show secret values. If your LiteLLM file contains a literal key, the output file also
contains it, and the report tells you to move it to an environment variable.

## 2. Read the report

The report has four groups:

| Group | What to do |
| --- | --- |
| Action needed | Do these steps before or just after the first start. |
| Not imported | DeltaLLM has no equivalent. Decide if you need a different setup. |
| Changed during import | The value or the behavior is different. Check that it is correct for you. |
| Notes | Information only. |

## 3. Set the environment and start DeltaLLM

Set these variables in addition to your provider keys:

| Variable | Value |
| --- | --- |
| `DELTALLM_MASTER_KEY` | A new admin key: at least 32 characters, with letters and digits. |
| `DELTALLM_SALT_KEY` | A new random value. Do not change it after the first start. |
| `DATABASE_URL` | The new PostgreSQL database. |
| `REDIS_URL` | The Redis server, unless the report shows `redis_host` settings. |

Then start DeltaLLM with the new `config.yaml`. See [Start with Docker](../getting-started/docker.md)
or [Kubernetes](kubernetes-deployment.md).

On the first start, DeltaLLM copies the imported `model_list` into the database. The file sets
`model_deployment_bootstrap_from_config: true` for this. After the first successful start, set
it to `false`, and then manage models in the Admin UI. See
[Model deployments](../configuration/models.md#bootstrap-behavior).

## 4. Create keys and move applications

The LiteLLM configuration file does not contain application keys, and DeltaLLM cannot use
LiteLLM key hashes. Choose one of these options for each application:

- **Create a new key.** See [Create an application key](../getting-started/first-api-key.md).
- **Keep the existing key value.** If you have the original `sk-...` value, create the
  DeltaLLM key with `POST /ui/api/keys` and the optional `key` field. The application then
  does not need a new key. See [Admin API: API keys](../api/admin.md#api-keys).

Then:

1. In each application, change the base URL to your DeltaLLM URL. Change the key if you
   created a new one. The request format stays the same: DeltaLLM uses the OpenAI API
   format and the model names from your LiteLLM `model_name` values.
2. Send a test request for each model. See [Check a deployment](../deployment/live-smoke-suite.md).

## What the tool converts

| LiteLLM | DeltaLLM |
| --- | --- |
| `model_list[].litellm_params` | `deltallm_params` with an explicit `provider` and the upstream model ID |
| Provider prefixes such as `together_ai/`, `fireworks_ai/`, `hosted_vllm/`, `lm_studio/`, `ollama_chat/`, `dashscope/` | The DeltaLLM provider names `together`, `fireworks`, `vllm`, `lmstudio`, `ollama`, `qwen` |
| `xai/`, `cerebras/`, `sambanova/`, `nvidia_nim/`, `moonshot/` | A generic OpenAI-compatible deployment with the provider's API base |
| A missing `api_key` | The environment variable that LiteLLM reads by default, such as `os.environ/GROQ_API_KEY` |
| Azure `api_base` and `api_version` | The Azure OpenAI v1 base (`https://<resource>.openai.azure.com/openai/v1`). DeltaLLM does not send `api-version`. |
| Ollama, vLLM and LM Studio `api_base` without a path | The same URL with `/v1` |
| `aws_region_name` | `region` |
| `temperature`, `top_p` and other request values in `litellm_params` | `model_info.default_params`. A value in the client request has priority. |
| `order` | `model_info.priority`. It applies with `routing_strategy: priority-based-routing`. |
| `model_info` prices and limits, `mode`, `access_groups`, `id` | The same `model_info` fields. `cache_read_input_token_cost` becomes `input_cost_per_token_cache_hit`, and `id` becomes `deployment_id`. |
| `router_settings` strategy, retries, timeout, cooldown, `model_group_alias` | The same `router_settings` keys. `usage-based-routing-v2` becomes `usage-based-routing`. |
| `fallbacks`, `context_window_fallbacks`, `content_policy_fallbacks` in `router_settings` or `litellm_settings` | `deltallm_settings`, merged into one list |
| `router_settings.redis_host`, `redis_port`, `redis_password` | `general_settings` |
| `litellm_settings.cache` and `cache_params` (`local`, `redis`) | `cache_enabled`, `cache_backend`, `cache_ttl` |
| Callbacks `langfuse`, `prometheus`, `otel`, `s3` | The same names in `deltallm_settings` |
| Guardrails `presidio` and `lakera` | The built-in DeltaLLM guardrails |
| `general_settings.alerting: [slack]` | Slack alerts with `SLACK_WEBHOOK_URL` |

## What does not move

- Vertex AI, Cohere, Hugging Face, SageMaker, watsonx, Replicate, Databricks and other providers
  that DeltaLLM does not support. The report lists each skipped deployment.
- Wildcard models (`model_name: "*"`). Add each model that you need.
- Users, teams, budgets and spend history. Create them again in DeltaLLM.
- Semantic, S3, GCS and Qdrant response caches.
- Custom Python hooks (`custom_auth`, custom callbacks and custom guardrails).
- `environment_variables`. The report lists the names. Set them in the DeltaLLM environment.
- Settings with no DeltaLLM equivalent, such as `drop_params`. The report lists each one.

## Related pages

- [Model deployments](../configuration/models.md)
- [Router settings](../configuration/router.md)
- [Choose what to turn on](../deployment/feature-switches.md)
