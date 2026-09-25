# Create a key for your application

Do not give an application the platform master key. Create a separate key that you can limit,
replace, or revoke without affecting the rest of DeltaLLM.

## Before you start

Every application key belongs to a team. If you do not have a team yet, create an
[organization](../admin-ui/organizations.md) and then a [team](../admin-ui/teams.md).

Use the addresses that match your setup:

| Setup | Admin UI | API |
| --- | --- | --- |
| Docker | `http://localhost:4002` | `http://localhost:4002` |
| Development setup | `http://localhost:5000` | `http://localhost:8000` |

## Create the key

1. Open the Admin UI address for your setup and sign in.
2. Open **API Keys**.
3. Select **Create key**.
4. Choose the team that owns the application.
5. Keep the owner set to **You**, or choose a service account for a shared application.
6. Give the key a clear name, such as `support-app-local`.
7. Add a budget, expiry date, or rate limit if the application needs one.
8. Choose which models or access groups the application may use.
9. Create the key and copy it immediately. The full key is shown only once.

Store the key in a secret manager or environment variable. Do not commit it to source control.

## Test the key

```bash
curl http://localhost:4002/v1/models \
  -H "Authorization: Bearer YOUR_APPLICATION_KEY"
```

The command uses the Docker API address. For the development setup, replace it with
`http://localhost:8000`. The response lists the models available to this key.

## Connect an application

DeltaLLM works with OpenAI SDKs. Use the application key you just created and the public model name
from the model list.

Install the SDK in your application project:

=== "Python"

    ```bash
    python3 -m pip install openai
    ```

=== "JavaScript"

    ```bash
    npm install openai
    ```

Then send a request. These examples use the Docker API address; use `http://localhost:8000/v1`
for the development setup.

=== "Python"

    ```python
    from openai import OpenAI

    client = OpenAI(
        base_url="http://localhost:4002/v1",
        api_key="YOUR_APPLICATION_KEY",
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Hello!"}],
    )
    print(response.choices[0].message.content)
    ```

=== "JavaScript"

    ```javascript
    import OpenAI from "openai";

    const client = new OpenAI({
      baseURL: "http://localhost:4002/v1",
      apiKey: "YOUR_APPLICATION_KEY",
    });

    const response = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "Hello!" }],
    });
    console.log(response.choices[0].message.content);
    ```

Replace `gpt-4o-mini` in either example if you chose a different public model name.

See [Application keys](../admin-ui/api-keys.md) for ownership, budgets, rate limits, and model
access. See the [admin API reference](../api/admin.md) if you want to create keys automatically.
