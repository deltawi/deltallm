# Find or add your first model {#check-or-add-your-first-model}

A model in DeltaLLM points to an AI provider and one of its models. Your application calls the
public name you choose, while DeltaLLM keeps the provider details and credentials separate.

## Find the sample model {#check-for-the-sample-model}

The Docker starter configuration can add a model named `gpt-4o-mini`.
Get the list of available models:

```bash
curl http://localhost:4002/v1/models \
  -H "Authorization: Bearer YOUR_MASTER_KEY"
```

Replace `YOUR_MASTER_KEY` with the `DELTALLM_MASTER_KEY` value from your `.env` file.

If the response includes `gpt-4o-mini`, your model is ready. Continue to
[Send your first request](quickstart.md).

## Add a model in the Admin UI

Use these steps if the model list is empty or you want to use a different provider:

1. Open `http://localhost:4002` in your browser.
2. Sign in with the administrator email and password from your `.env` file.
3. Open **AI Gateway**.
4. Open **Models**.
5. Select **Add model**.
6. Enter a public name. Use `gpt-4o-mini` to use the examples in this guide.
7. Select the provider.
8. Enter the provider's model name.
9. Add the provider API key or select a saved provider credential.
10. Keep the type set to **Chat** for a standard text model.
11. Save the model.
12. Wait until the model shows a healthy status.

Run the model-list command again. The response should include the public name you chose.

Continue to [Send your first request](quickstart.md).

## If the model does not appear

- Check that the provider API key is present and valid.
- Check that the provider model name is correct.
- Review the container logs for a connection or sign-in error.
- See [Models in the Admin UI](../admin-ui/models.md) for more help.

For every available setting, see the [model deployment reference](../configuration/models.md).
