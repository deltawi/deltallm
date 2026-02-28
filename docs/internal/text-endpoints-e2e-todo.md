# Text Endpoints E2E Validation TODO

## Objective

Validate end-to-end flow using master key + scoped virtual key:
- model declaration
- org/team/user/key provisioning
- `/v1/completions` and `/v1/responses` invocation
- usage/monitoring evidence

## Checklist

- [x] Bring up dedicated local app instance for E2E
- [x] Declare OpenAI + Groq models via admin API (master key)
- [x] Create organization, team, and user
- [x] Create scoped API key attached to created user/team
- [x] Call `/v1/completions` with scoped key
- [x] Call `/v1/responses` with scoped key
- [x] Verify records in spend logs / summary / metrics
- [x] Capture findings, constraints, and cleanup

## Run Notes (2026-02-27)

- Dedicated app started on `127.0.0.1:4010` using temporary config `/tmp/deltallm-e2e-config.yaml`.
- Test identity artifacts:
  - `team_id`: `team-e2e1772216351`
  - `user_id`: `user-e2e1772216351`
  - API key hash created (raw key generated and used for calls).
- Endpoint results:
  - `/v1/completions` with Groq model: `200`
  - `/v1/responses` with Groq model: `200`
  - `/v1/completions` with OpenAI model: upstream returned `400` (surfaced as gateway `503/service_unavailable`)
- Monitoring evidence:
  - `/ui/api/logs` contains 2 entries for the new team and user (50 + 52 tokens).
  - `/metrics` contains `deltallm_requests_total` lines for the E2E models/team/user labels.
