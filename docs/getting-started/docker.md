# Start with Docker

Docker Compose starts DeltaLLM and the services it needs. This is the easiest way to try DeltaLLM
on your computer.

## Before you start

You need:

- Docker with Docker Compose v2 or later
- Git
- an OpenAI API key for the sample model

## 1. Download DeltaLLM

```bash
git clone https://github.com/deltawi/deltallm.git
cd deltallm
```

## 2. Copy the starter configuration

```bash
cp config.example.yaml config.yaml
```

The starter configuration includes an OpenAI model named `gpt-4o-mini`. To add that model to the
database the first time DeltaLLM starts, make sure `config.yaml` contains:

```yaml
general_settings:
  model_deployment_source: db_only
  model_deployment_bootstrap_from_config: true
```

After the first successful start, you can change
`model_deployment_bootstrap_from_config` back to `false`.

## 3. Add your secrets

Create a `.env` file in the repository root with these values:

```env
DELTALLM_MASTER_KEY=replace-with-a-generated-master-key
DELTALLM_SALT_KEY=replace-with-a-generated-salt-key
OPENAI_API_KEY=replace-with-your-openai-key
PLATFORM_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
PLATFORM_BOOTSTRAP_ADMIN_PASSWORD=replace-with-a-strong-password
```

Generate the master key and salt instead of writing them yourself:

```bash
python3 -c 'import secrets; print("DELTALLM_MASTER_KEY=sk-" + secrets.token_hex(20) + "A1")'
python3 -c 'import secrets; print("DELTALLM_SALT_KEY=" + secrets.token_hex(32))'
```

Copy the generated values into `.env`. Keep this file private and do not commit it to source
control.

The admin email and password create the first account you can use to sign in to the Admin UI.

## 4. Start DeltaLLM

```bash
docker compose --profile single up -d --build
```

The first start may take a few minutes while Docker downloads and builds the images. When it
finishes, DeltaLLM is available at `http://localhost:4002`.

Docker also starts PostgreSQL and Redis. You do not need to install or configure them separately.

## 5. Check that it works

Check the service:

```bash
curl http://localhost:4002/health/liveliness
```

You should receive:

```json
{
  "status": "ok"
}
```

Now continue to [Check or add your first model](first-model.md).

## If DeltaLLM does not start

View the container logs:

```bash
docker compose --profile single logs deltallm
```

Check that:

- the values in `.env` are not placeholders
- the master key is at least 32 characters and contains letters and numbers
- the OpenAI API key is valid
- port `4002` is not already in use

For multi-instance testing, operational limits, and production guidance, see
[Docker deployment](../deployment/docker.md).
