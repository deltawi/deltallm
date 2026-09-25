# Add your first guardrail

Guardrails check prompts or model responses for content you do not want to allow. Start with one
rule, test it with sample content, and then decide where it should apply.

## Before you start

You need administrator access and one working model. Use test content rather than real personal or
sensitive information.

## Choose what to check

| Goal | Guardrail | When it runs |
| --- | --- | --- |
| Find or hide personal information | **PII Detection** | Before or after the model call |
| Stop likely prompt-injection attempts | **Prompt Injection Detection** | Before the model call |

For your first test, use **PII Detection** before the model call. This prevents sensitive text from
reaching the model provider.

## Create the guardrail

1. Open the Admin UI, then select **Guardrails**.
2. Select **Add Guardrail**, then choose **PII Detection**.
3. Set **Mode** to **Pre-call**.
4. Set **Action** to **Block**.
5. Leave the guardrail enabled by default.
6. Save it.

The standard installation can detect common items such as email addresses, phone numbers, US Social
Security numbers, credit-card numbers, and IP addresses. More detection options are available when
the full Presidio package is installed.

## Test it safely

First, send a normal request and confirm that it succeeds. Then send a request containing clearly
fake personal information, such as:

```text
My test email is example@example.com. Summarize this sentence.
```

The second request should be blocked before it reaches the model. Check the audit information to
confirm which guardrail handled it.

## Limit where it applies

A guardrail can apply everywhere or only to one organization, team, or application key. Start with a
test team or key when you are changing an existing system. Expand it only after normal requests and
expected violations both behave correctly.

## Next steps

- [Manage guardrails in the Admin UI](../admin-ui/guardrails.md)
- [Guardrail settings and behavior](../features/guardrails.md)
- [Administration API](../api/admin.md#guardrails)
