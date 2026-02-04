# Provider Configuration

DeltaLLM supports 8+ LLM providers out of the box.

## Supported Providers

| Provider | Environment Variable | Notes |
|----------|---------------------|-------|
| [OpenAI](openai.md) | `OPENAI_API_KEY` | GPT-4, GPT-3.5, Embeddings |
| [Anthropic](anthropic.md) | `ANTHROPIC_API_KEY` | Claude 3.5, Claude 3 |
| [Azure OpenAI](azure.md) | `AZURE_API_KEY` | Enterprise OpenAI |
| [AWS Bedrock](bedrock.md) | AWS credentials | Claude, Llama, Mistral |
| [Google Gemini](gemini.md) | `GEMINI_API_KEY` | Gemini 1.5 Pro |
| [Cohere](cohere.md) | `COHERE_API_KEY` | Command-R |
| [Mistral AI](mistral.md) | `MISTRAL_API_KEY` | Mistral Large |
| [Groq](groq.md) | `GROQ_API_KEY` | Fast inference |

## Provider Naming

Use the `provider/model-name` format:

```python
# OpenAI
"gpt-4o"
"gpt-4-turbo"
"text-embedding-3-small"

# Anthropic (prefix required)
"anthropic/claude-3-5-sonnet-20241022"
"anthropic/claude-3-opus-20240229"

# Azure (prefix required)
"azure/your-deployment-name"

# AWS Bedrock (prefix required)
"bedrock/anthropic.claude-3-sonnet"

# Google (prefix required)
"gemini/gemini-1.5-pro"

# Cohere (prefix required)
"cohere/command-r"

# Mistral (prefix required)
"mistral/mistral-large"

# Groq (prefix required)
"groq/llama-3.1-70b"
```

## Configuration File

The recommended way to configure providers is via YAML:

```yaml
# config.yaml
model_list:
  # OpenAI models
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: ${OPENAI_API_KEY}
      rpm: 1000  # Rate limit
  
  # Anthropic models
  - model_name: claude-3-sonnet
    litellm_params:
      model: anthropic/claude-3-5-sonnet-20241022
      api_key: ${ANTHROPIC_API_KEY}
  
  # Azure OpenAI
  - model_name: gpt-4-azure
    litellm_params:
      model: azure/your-deployment
      api_base: https://your-resource.openai.azure.com
      api_key: ${AZURE_API_KEY}
      api_version: "2024-02-01"

router_settings:
  routing_strategy: "least-busy"
  num_retries: 3
  timeout: 30
```

## Environment Variables

You can also configure providers via environment variables:

```bash
# OpenAI
export OPENAI_API_KEY="sk-..."

# Anthropic
export ANTHROPIC_API_KEY="sk-ant-..."

# Azure
export AZURE_API_KEY="..."
export AZURE_API_BASE="https://your-resource.openai.azure.com"

# AWS (uses default AWS credential chain)
export AWS_REGION="us-east-1"
# Or use ~/.aws/credentials

# Google
export GEMINI_API_KEY="..."

# Cohere
export COHERE_API_KEY="..."

# Mistral
export MISTRAL_API_KEY="..."

# Groq
export GROQ_API_KEY="gsk_..."
```

## Adding Custom Models

To add a custom model or fine-tune:

```yaml
model_list:
  - model_name: my-fine-tuned-model
    litellm_params:
      model: openai/your-fine-tuned-model-id
      api_key: ${OPENAI_API_KEY}
```

Then use it like any other model:

```python
response = await completion(
    model="my-fine-tuned-model",
    messages=[...],
)
```

## Provider-Specific Parameters

Some providers support additional parameters:

### Anthropic - Extended Thinking

```python
response = await completion(
    model="anthropic/claude-3-5-sonnet",
    messages=[...],
    extra_body={
        "thinking": {
            "type": "enabled",
            "budget_tokens": 16000
        }
    }
)
```

### OpenAI - JSON Mode

```python
response = await completion(
    model="gpt-4o",
    messages=[...],
    response_format={"type": "json_object"}
)
```

### Gemini - Safety Settings

```python
response = await completion(
    model="gemini/gemini-1.5-pro",
    messages=[...],
    safety_settings=[
        {"category": "HARM_CATEGORY_DANGEROUS", "threshold": "BLOCK_NONE"}
    ]
)
```

## Fallback Configuration

Configure fallbacks between providers:

```yaml
model_list:
  - model_name: primary-gpt4
    litellm_params:
      model: openai/gpt-4o
    fallback: [backup-claude, backup-gemini]
  
  - model_name: backup-claude
    litellm_params:
      model: anthropic/claude-3-sonnet
  
  - model_name: backup-gemini
    litellm_params:
      model: gemini/gemini-1.5-pro
```

## Next Steps

- See individual provider pages for detailed setup instructions
- Learn about [load balancing and routing](../routing.md)
- Configure [caching](../caching.md) to reduce costs
