# Understand the system

These pages explain the main ideas behind DeltaLLM. Read them when you want to understand why the
system behaves a certain way, rather than follow a setup procedure.

## Start here

- [Architecture](architecture.md) explains the main parts of DeltaLLM and what each part owns.
- [What happens to a request](request-lifecycle.md) follows one request from authentication to the
  provider response.
- [Accounts, teams, and access](tenancy-and-access.md) explains how organizations, teams, users,
  and keys work together.

## Four useful terms

- **Model deployment:** the connection to one real model at one provider.
- **Public model name:** the name an application sends to DeltaLLM.
- **Route group:** several deployments presented as one target, with routing and failover rules.
- **Application key:** a secret that gives one application controlled access to DeltaLLM.

If you are setting up the product, use [Get started](../getting-started/index.md). If you need an
exact field or endpoint, use [Reference](../reference/index.md).
