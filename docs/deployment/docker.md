# Docker Deployment

DeltaLLM provides official Docker images for easy deployment.

## Quick Start

```bash
docker run -p 8000:8000 \
  -e OPENAI_API_KEY="sk-..." \
  ghcr.io/mehditantaoui/deltallm:latest
```

Test the deployment:

```bash
curl http://localhost:8000/health
```

## With Configuration File

```bash
# Create a config file
cat > config.yaml << 'EOF'
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
EOF

# Run with config
docker run -p 8000:8000 \
  -e OPENAI_API_KEY="sk-..." \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  -v $(pwd)/config.yaml:/app/config.yaml \
  ghcr.io/mehditantaoui/deltallm:latest \
  deltallm server --config /app/config.yaml
```

## Docker Compose

For a complete deployment with PostgreSQL and Redis:

```yaml
# docker-compose.yml
version: '3.8'

services:
  deltallm:
    image: ghcr.io/mehditantaoui/deltallm:latest
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/deltallm
      - REDIS_URL=redis://redis:6379/0
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    volumes:
      - ./config.yaml:/app/config.yaml
    depends_on:
      - postgres
      - redis
    command: deltallm server --config /app/config.yaml --port 8000

  postgres:
    image: postgres:15
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
      - POSTGRES_DB=deltallm
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
```

Run:

```bash
docker-compose up -d
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `OPENAI_API_KEY` | OpenAI API key | If using OpenAI |
| `ANTHROPIC_API_KEY` | Anthropic API key | If using Anthropic |
| `DATABASE_URL` | PostgreSQL connection string | For persistence |
| `REDIS_URL` | Redis connection string | For caching |
| `SECRET_KEY` | Secret for JWT tokens | Recommended |

## Building from Source

```bash
docker build -t deltallm:local .
docker run -p 8000:8000 deltallm:local
```

## Production Deployment

### With Reverse Proxy (nginx)

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./ssl:/etc/nginx/ssl
    depends_on:
      - deltallm

  deltallm:
    image: ghcr.io/mehditantaoui/deltallm:latest
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/deltallm
      - REDIS_URL=redis://redis:6379/0
    deploy:
      replicas: 2
    depends_on:
      - postgres
      - redis
```

### Health Checks

The container includes health checks:

```bash
# Check health
docker exec <container> curl -f http://localhost:8000/health

# View logs
docker logs -f <container>
```

## Troubleshooting

### Container won't start

```bash
# Check logs
docker logs <container>

# Verify environment variables
docker exec <container> env | grep API_KEY
```

### Connection issues

```bash
# Test from inside container
docker exec -it <container> /bin/sh
wget -O- http://localhost:8000/health
```

## Next Steps

- [Kubernetes Deployment](kubernetes.md)
- [Cloud Deployment](cloud.md)
- [Configuration Reference](../api/index.md)
