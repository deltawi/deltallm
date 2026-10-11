---
title: Video tutorials
description: Short videos that show the main DeltaLLM workflows in the Admin UI.
status: stable
audience: developers, administrators
---

# Video tutorials

Each video shows one task from start to finish in the Admin UI and takes less than a minute and a
half. Watch them in order if you are new to DeltaLLM. Every video also appears on the guide for its
feature, next to the written steps.

All videos are on the [DeltaLLM YouTube channel](https://www.youtube.com/@deltallm).

## 1. Add a model and call it

Connect a provider model, give it a public name, create an application key, and send a first
request with the OpenAI SDK.

--8<-- "videos/adding-models.html"

Written steps: [Find or add your first model](../getting-started/first-model.md) and
[Add and manage models](../guides/admin-models.md).

## 2. Route traffic and fail over

Put several deployments behind one public name with a route group, send traffic to a backup when a
provider fails, and let the auto router pick a model for each request.

--8<-- "videos/routing-and-failover.html"

Written steps: [Route traffic and handle failures](../guides/routing-and-failover.md) and
[Manage route groups](../guides/admin-route-groups.md).

## 3. Set budgets and rate limits

Cap spending with organization and team budgets, then assign a tier that sets requests and tokens
per minute for each model.

--8<-- "videos/budgets-and-rate-limits.html"

Written steps: [Set budgets](../guides/admin-budgets.md),
[Set rate limits](../guides/admin-rate-limits.md), and [Set up plans and tiers](../guides/admin-tiers.md).

## 4. Run a batch job

Upload a file of inputs, run an embeddings batch for a retrieval (RAG) index, and download the
results when the job finishes.

--8<-- "videos/batch-jobs.html"

Written steps: [Process work in batches](../guides/batching.md) and
[Batch Jobs](../admin-ui/batch-jobs.md).
