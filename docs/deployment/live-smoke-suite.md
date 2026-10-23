# Check a deployment with the live smoke suite

The live smoke suite does a short end-to-end check of a running gateway. Run it after each
deployment or upgrade, before you send customer traffic to the new release.

The suite uses the master key only for admin setup. It does the inference calls with an API key
that a test user creates, as a customer does. Thus it checks the same path that real keys use:
tier access, organization and team scope, key authentication, routing, and providers.

## What the suite does

1. It checks `/health/liveliness` and lists all models and route groups (callable targets).
2. It creates the tier `<prefix>-all-models`, or uses it again. The active tier version must
   allow every model and route group. If a name is missing, the suite makes a new version with
   the current list and activates it. The tier holds the names that callers use, not provider
   model IDs.
3. It creates the organization `<prefix>-org-<6 hex characters>` with this tier as its primary
   tier. It also gives the organization access to all models. When the tier policy mode is
   `enforce`, the gateway refuses this grant, and the tier alone controls access.
4. It creates the team `<organization ID>-team` and the test user `<prefix>-user@example.com`.
   The test user is an organization member and a team admin of this team. The test user gets a
   new random password on each run. The password stays in memory only.
5. The test user signs in and creates an API key, `<prefix>-key`. The key expires after six hours.
   When password sign-in is off (`general_settings.password_login_enabled: false`), the test user
   cannot sign in. Then the suite uses the master key to create the key, with the test user as
   the owner. The key has the same team and organization, so the model checks do not change.
6. It waits until `/v1/models` for that key lists every model that the test organization can
   use (30 seconds maximum).
7. It calls each model once, with the call that agrees with its mode:

    | Mode | Call |
    | --- | --- |
    | `chat` | `/v1/chat/completions`, one normal call and one streaming call |
    | `embedding` | `/v1/embeddings` |
    | `audio_speech` | `/v1/audio/speech` |
    | `audio_transcription` | `/v1/audio/transcriptions` with one second of silence |
    | `image_generation` | `/v1/images/generations` |
    | `rerank` | `/v1/rerank` |
    | `realtime` | Not called (the result is `skip`) |

    If the deployments of one model or route group have different modes, the suite does not
    call it, and the result is `fail`. `--skip-mode` does not skip this result. Make the
    deployments agree on one mode.

    An account can create its own model or route group. Only the owner, and the teams and
    organizations that the owner shares it with, can call it, unless it is public. The tier does
    not change this. The suite does not change the sharing of other accounts. It does not call
    such a model, and the result is `skip`.

8. It runs one chat batch and one embedding batch with two items each: upload, create, poll,
   and read the output file. Each batch uses a model that passed in step 7. If a batch does not
   finish in time, or a batch step fails, the suite cancels the batch.
9. It deletes the API key, also when a step fails.

Chat calls use `max_tokens: 32`. A reasoning model can use all of these tokens before it writes
text. The suite then shows `warn` with "empty content", and the check does not fail.

## Run the suite

The suite is a script that you start manually. It does not run in CI. Use a checkout of the
same release, and give the gateway URL with `--base-url`:

```bash
uv sync --frozen
uv run python -m scripts.live_smoke --base-url https://gateway.example.com
```

The suite asks for the master key and does not show what you type. You can also put the key in
the `DELTALLM_MASTER_KEY` environment variable before you start the suite. The suite does not
accept the key as an argument, because the shell history keeps arguments. It does not write the
key to a file.

The suite removes spaces and line breaks at the start and end of the key. The key must be one
line of printable ASCII characters with no spaces. The suite replaces the master key and the test
API key with `[redacted]` in all results.

Each setup check shows one line when it completes. The model checks run at the same time, and
their lines show when all model checks complete. At the end, the suite shows a table of results
for each model and the total. The exit code is:

| Exit code | Meaning |
| --- | --- |
| `0` | All checks passed. |
| `1` | One or more checks failed. The list at the end shows them. |
| `2` | The suite did not start, for example because it got no master key. |

## Options

| Option | Default | Use |
| --- | --- | --- |
| `--prefix` | `live-smoke` | Name prefix of all test resources. Use a different prefix to keep two sets apart. |
| `--model NAME` | All | Call only these models or route groups. You can repeat it or use commas. The tier still includes all models. |
| `--skip-mode MODE` | None | Do not call models of this mode, for example `image_generation`. |
| `--no-batch` | Off | Do not run batch jobs. |
| `--batch-model NAME` | None | Chat model for the chat batch. Without this option, the suite uses the first chat model that passed in alphabetical order. It uses a route group only when no plain model passed. |
| `--batch-timeout` | `300` | Seconds to wait for each batch. |
| `--timeout` | `60` | Seconds for each HTTP request. |
| `--max-tokens` | `32` | `max_tokens` for chat calls. |
| `--parallel` | `4` | Number of models that the suite calls at the same time (1 to 16). |
| `--tts-voice` | `alloy` | Voice for speech models. |
| `--image-size` | Provider default | Size for image models, for example `1024x1024`. |
| `--openai-sdk` | Off | Also call each model with the official OpenAI Python SDK. See [Check with the OpenAI SDK](#check-with-the-openai-sdk). |
| `--json-output PATH` | None | Also write the results to a JSON file. The file does not contain the master key or the test API key. |
| `--cleanup` | Off | After the run, also delete the organization, test user, and tier. |
| `--cleanup-only` | Off | Delete the test resources and do no checks. |

## Check with the OpenAI SDK

The model checks send plain HTTP requests. Most customers use the official
[OpenAI Python SDK](https://github.com/openai/openai-python), which parses each answer into its
own types. With `--openai-sdk`, the suite also calls each model with the SDK, after the HTTP
checks and with the same test API key:

| Mode | SDK call |
| --- | --- |
| `chat` | `chat.completions.create`, one normal call and one streaming call |
| `embedding` | `embeddings.create` with the SDK default (base64, which the SDK changes back to floats) |
| `audio_speech` | `audio.speech.create` |
| `audio_transcription` | `audio.transcriptions.create` with one second of silence |
| `image_generation` | `images.generate` |

The SDK has no rerank call, so rerank models get only the HTTP check. The SDK lines start with
`sdk`, for example `sdk chat stream`. A setup line shows the SDK version. The SDK does not
retry failed calls.

The gateway does not depend on the SDK. Install it for one run only. This command uses the SDK
version that the [OpenAI SDK compatibility](../api/openai-sdk-compatibility.md) tests use:

```bash
uv run --with-requirements tests/openai_sdk/requirements.txt \
  python -m scripts.live_smoke --base-url https://gateway.example.com --openai-sdk
```

To check the newest SDK, use `uv run --with openai` instead. Without the SDK, `--openai-sdk`
stops the suite with exit code `2`.

The [OpenAI SDK compatibility](../api/openai-sdk-compatibility.md) tests in CI use a fake
provider. This pass uses the real providers of a deployment. Thus, it finds answers that the
SDK cannot read, for example a stream chunk with an unusual shape from one provider.

## Run it again

You can run the suite many times. A second run finds the tier, organization, team, and user by
their names and uses them again. It finds the organization by the `<prefix>-org` ID prefix. It
deletes test keys that an interrupted run left. It activates a new tier version only when a model
is missing from the tier. A model that was removed from the gateway stays in the tier until a
new model causes a new version.

## Remove the test resources

Without `--cleanup`, the tier, organization, team, and test user stay on the gateway. This makes
the next run faster. To remove them, run the suite with `--cleanup`, or run it with
`--cleanup-only`.

The suite deletes the organization with the normal [organization deletion](../features/organization-deletion.md)
job, with an immediate (expedited) deletion. The job also removes the team, memberships, and
keys. The suite waits up to two minutes for the job. If the job does not complete in that time,
the result is `warn`, and the test user or tier can stay. Run `--cleanup-only` again after
the job completes.

The gateway never uses a deleted organization ID again. Thus, the next run creates a new
organization with a new random suffix.

Organization deletion requests are off by default (`organization_deletion_requests_enabled`).
When they are off, the suite cannot delete the organization. It removes the team and the tier
assignment of the organization, then deletes the test user and the tier. The result is `warn`.
The empty organization stays, and the next run uses it again.

## Cost and side effects

- Each run sends a small number of real requests to each provider. Image and speech models can
  cost more than chat models. Use `--skip-mode` to leave them out.
- The usage pages and the audit log show the test traffic for the `<prefix>-org-...` organization.
- The suite changes only resources that have the suite prefix. It does not change other tiers,
  organizations, or keys.

## Read the results

| Result | Possible cause |
| --- | --- |
| `403 ... not allowed for this key` | The tier or the organization does not allow the model. Make sure that the model name is in the tier. |
| `401 Invalid API key` on all models | The key did not reach all replicas. Make sure that the key cache and Redis are healthy. |
| `503` with an error code | The gateway refused the request. Look for the request ID and the error code in the gateway logs. |
| `key sees models: missing ...` | The tier or grant changes did not reach all replicas in 30 seconds, or the models are not visible to the organization. |
| Batch `still validating` or `still in_progress` | The batch worker does not run, or it cannot claim work. See [Batch processing](../guides/batching.md). |
