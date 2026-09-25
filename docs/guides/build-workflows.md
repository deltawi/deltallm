# Build complete workflows

These checklists connect the Admin UI, API, and verification steps for common application work.
Use test accounts and application keys while setting them up.

## Add a model and call it

1. Follow [Choose and connect a model](models-and-providers.md).
2. Wait for the model to report healthy.
3. Create an application key that can use the model.
4. Follow [Send your first request](../getting-started/quickstart.md), replacing the sample model
   name with your public model name.
5. Confirm that Usage and spending records the expected key, team, and model.

Success means the request works with the application key and fails with an invalid key.

## Add a backup or canary

1. Create two healthy deployments with compatible model types.
2. Follow [Route traffic and handle failures](routing-and-failover.md).
3. Test the policy before publishing it.
4. Send requests through the route-group key.
5. Confirm that an unavailable primary uses the expected backup.

Success means applications keep using one public name while DeltaLLM selects the intended
deployment.

## Add a prompt template

1. Create a template and its first version in [Prompts](../admin-ui/prompt-registry.md).
2. Define the required variables and test them with representative values.
3. Point a stable label such as `production` at the reviewed version.
4. Bind the prompt to its supported route group and scope.
5. Create and test a second version before moving the label.

Success means missing variables fail before the provider call and moving the label changes new
requests without changing application code.

## Add a guardrail

1. Start with [Add your first guardrail](guardrails.md).
2. Assign the guardrail to a test organization, team, or application key.
3. Send one normal request and one synthetic request that should trigger the rule.
4. Check the response and authorized audit information.
5. Test a neighboring scope that should not receive the same rule.

Success means normal traffic works, the test violation receives the configured action, and the
policy does not leak into another scope.

## Run a batch

1. Follow [Process work in batches](batching.md).
2. Open [Batch jobs](../admin-ui/batch-jobs.md) and find the batch ID.
3. Compare the completed and failed counts with the number of input lines.
4. Download both output and error files when present.

Success means every input line is accounted for and another tenant cannot open the batch.

## Connect one MCP tool

1. Follow [Connect your first MCP server](../getting-started/mcp-quickstart.md).
2. Allow only the tool needed by the test application.
3. Add reasonable request and concurrency limits.
4. Test with one allowed application key and one key outside the configured scope.

Success means the allowed key can discover and call the tool while the other key cannot see it.
