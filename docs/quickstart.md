# Quick Start Guide

Get DeltaLLM running in 5 minutes.

## Installation

### Using pip

```bash
pip install deltallm
```

### Using uv (recommended)

```bash
uv pip install deltallm
```

### From Source

```bash
git clone https://github.com/mehditantaoui/deltallm.git
cd deltallm
pip install -e "."
```

## Quick Examples

### 1. Basic Completion

```python
import asyncio
from deltallm import completion

async def main():
    response = await completion(
        model="gpt-4o",
        messages=[{"role": "user", "content": "What is the capital of France?"}],
        api_key="sk-..."
    )
    print(response.choices[0].message.content)

asyncio.run(main())
```

### 2. Multi-Provider Example

```python
import asyncio
from deltallm import completion

async def main():
    # Same code works with any provider
    
    # OpenAI
    openai_response = await completion(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello"}],
        api_key="sk-..."
    )
    
    # Anthropic
    anthropic_response = await completion(
        model="claude-3-sonnet",
        messages=[{"role": "user", "content": "Hello"}],
        api_key="sk-ant-..."
    )
    
    # Google Gemini
    gemini_response = await completion(
        model="gemini-1.5-pro",
        messages=[{"role": "user", "content": "Hello"}],
        api_key="..."
    )
    
    print("OpenAI:", openai_response.choices[0].message.content)
    print("Anthropic:", anthropic_response.choices[0].message.content)
    print("Gemini:", gemini_response.choices[0].message.content)

asyncio.run(main())
```

### 3. Streaming Responses

```python
import asyncio
from deltallm import completion

async def main():
    response = await completion(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Tell me a story"}],
        api_key="sk-...",
        stream=True
    )
    
    async for chunk in response:
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)

asyncio.run(main())
```

### 4. Using Embeddings

```python
import asyncio
from deltallm import embedding

async def main():
    response = await embedding(
        model="text-embedding-3-small",
        input=["Hello world", "Goodbye world"],
        api_key="sk-..."
    )
    
    for item in response.data:
        print(f"Embedding {item.index}: {item.embedding[:5]}...")

asyncio.run(main())
```

## Running the Proxy Server

The proxy server provides an OpenAI-compatible REST API.

### 1. Create Configuration

```yaml
# config.yaml
model_list:
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: ${OPENAI_API_KEY}
  
  - model_name: claude-3-sonnet
    litellm_params:
      model: anthropic/claude-3-5-sonnet-20241022
      api_key: ${ANTHROPIC_API_KEY}

router_settings:
  routing_strategy: "least-busy"
  timeout: 30
  num_retries: 3
```

### 2. Start the Server

```bash
# Set environment variables
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."

# Start the server
deltallm server --config config.yaml --port 8000

# Or with Docker
docker run -p 8000:8000 \
  -e OPENAI_API_KEY="sk-..." \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  -v $(pwd)/config.yaml:/app/config.yaml \
  ghcr.io/mehditantaoui/deltallm:latest
```

### 3. Use the API

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000",
    api_key="your-deltallm-key"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}]
)

print(response.choices[0].message.content)
```

## Next Steps

- [Configure Providers](providers/) - Set up additional providers
- [Set Up Routing](routing.md) - Configure load balancing and fallbacks
- [Enable Caching](caching.md) - Speed up responses and reduce costs
- [Deploy to Production](deployment/) - Production deployment guides
