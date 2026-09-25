# Check or add your first model

A model in DeltaLLM points to an AI provider and one of its models. Your application calls the
public name you choose, while DeltaLLM keeps the provider details and credentials separate.

## Check for the sample model

If you used the Docker starter configuration, it may have added a model named `gpt-4o-mini` for
you. Check the available models:

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
3. Open **AI Gateway**, then **Models**.
4. Select **Add model**.
5. Enter a public name. Use `gpt-4o-mini` if you want to follow the examples in this guide.
6. Choose the provider and enter the provider's model name.
7. Add the provider API key or choose a saved provider credential.
8. Keep the type set to **Chat** for a normal text model.
9. Save the model and wait for it to show as healthy.

Run the model-list command again. The response should include the public name you chose.

Continue to [Send your first request](quickstart.md).

## If the model does not appear

- Check that the provider API key is present and valid.
- Check that the provider model name is correct.
- Review the container logs for a connection or sign-in error.
- See [Models in the Admin UI](../admin-ui/models.md) for more help.

For every available setting, see the [model deployment reference](../configuration/models.md).
