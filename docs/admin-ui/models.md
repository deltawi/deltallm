# Models

The Models page is the deployment registry for concrete provider-backed model endpoints.

Deployments created here are the building blocks used by [Route Groups](route-groups.md).

![Models](images/models-list.png)

## What operators manage here

- Provider model identity, such as `gpt-4o-mini` or `llama-3.1-8b-instant`
- Provider connection details and credentials
- Workload type (`chat`, `embedding`, `image_generation`, `audio_speech`, `audio_transcription`, `rerank`)
- Pricing metadata for spend calculation
- Default parameters injected into requests

## Table columns

- **Model Name**: the provider-facing model name operators recognize
- **Type**: workload mode
- **Provider**: OpenAI, Groq, and other configured backends
- **Deployment ID**: stable internal identifier used by route groups
- **Health**: current runtime availability

## Recommended workflow

1. Add the deployment with the correct provider model ID and credentials
2. Verify the deployment becomes healthy
3. Reuse the deployment inside one or more route groups

## Operational notes

- Prefer readable deployment IDs because route-group membership uses them directly
- If you rename or delete a deployment, update any dependent route groups in the same change window
