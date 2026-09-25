# Add and manage models

The Models page connects DeltaLLM to a provider model. Applications call the public model name you
choose instead of the provider's model name.

## Add a model

You need platform administrator access and a provider key or saved provider credential.

1. Open **AI Gateway**, then **Models**.
2. Select **Add Model**.
3. Keep **Model type** set to **Chat** for a normal text model.
4. Enter the public name that applications will use.
5. Choose the provider and its model.
6. Enter the provider key or choose a named credential.
7. Select **Create** and wait for the model to report healthy.

## Give an application access

Adding a model does not automatically make it available to every application. Make sure the
organization, team, and application key can use its public name.

Test it in [Playground](../admin-ui/playground.md) with a short-lived application key. Success means
the model returns an answer and the request appears under **Usage**.

## Change or remove a model

Before editing a public name or deleting a model, check application usage and route-group
membership. A public-name change requires applications and access rules to use the new name.

## Learn more

- [Choose and connect a model](models-and-providers.md)
- [Model administration details](../admin-ui/models.md)
- [Model configuration reference](../configuration/models.md)
