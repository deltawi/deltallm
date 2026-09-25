# Reuse a provider credential

Save a provider credential when several models use the same provider key or endpoint. You can then
rotate the secret once instead of editing every model.

For a single model, entering the provider key directly in the model form is simpler.

## Before you start

You need platform administrator access and the provider's connection details.

## Save the credential

1. Open **AI Gateway**, then **Named Credentials**.
2. Select **Create Named Credential**.
3. Enter a clear name and choose the provider.
4. Enter the provider key and any required API address, region, or version.
5. Select **Create Credential**.

Secret values are hidden after they are saved.

## Use it with a model

1. Open **AI Gateway**, then **Models**.
2. Add a model or edit an existing model.
3. Set the credential source to **Named Credential**.
4. Choose the saved credential.
5. Save the model and wait for it to report healthy.

## Rotate the secret safely

1. Create the replacement secret at the provider without disabling the current secret.
2. Edit the named credential and save the replacement.
3. Test every linked model type and provider region.
4. Disable the old provider secret only after the tests pass.

## Learn more

- [Provider credential details](../admin-ui/named-credentials.md)
- [Choose and connect a model](models-and-providers.md)
- [Production secret handling](../security/hardening.md#secret-lifecycle)
