# Process work in batches

Use a batch when you have many requests and do not need each answer immediately. You upload a file,
start the batch, wait for it to finish, and download the results.

## Before you start

Enable batch processing in `config.yaml`, then restart DeltaLLM:

```yaml
general_settings:
  embeddings_batch_enabled: true
```

Set the address and application key for your environment:

```bash
export BASE_URL="http://localhost:4002"
export API_KEY="YOUR_APPLICATION_KEY"
```

Use `http://localhost:8000` for the manual development setup.

## 1. Create the input file

Save the following lines as `input.jsonl`. Each line is one request and must have a unique
`custom_id`.

```jsonl
{"custom_id":"chat-1","url":"/v1/chat/completions","body":{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Describe DeltaLLM in one sentence."}]}}
{"custom_id":"chat-2","url":"/v1/chat/completions","body":{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Write a short welcome message."}]}}
```

Replace `gpt-4o-mini` if your public model has a different name.

## 2. Upload the file

```bash
curl "$BASE_URL/v1/files" \
  -H "Authorization: Bearer $API_KEY" \
  -F "purpose=batch" \
  -F "file=@input.jsonl"
```

Copy the `id` from the response. It looks like `file_abc123`.

## 3. Start the batch

```bash
curl "$BASE_URL/v1/batches" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input_file_id": "file_abc123",
    "endpoint": "/v1/chat/completions",
    "completion_window": "24h"
  }'
```

Copy the batch `id` from the response.

## 4. Wait for completion

```bash
curl "$BASE_URL/v1/batches/BATCH_ID" \
  -H "Authorization: Bearer $API_KEY"
```

Repeat this check until `status` is `completed`, `failed`, `cancelled`, or `expired`.

## 5. Download the results

Copy `output_file_id` from the completed batch, then run:

```bash
curl "$BASE_URL/v1/files/OUTPUT_FILE_ID/content" \
  -H "Authorization: Bearer $API_KEY" \
  -o output.jsonl
```

Each output line includes the `custom_id` from its input request. If the batch also has an
`error_file_id`, download that file the same way to inspect failed items.

## Next steps

- [View batch jobs in the Admin UI](../admin-ui/batch-jobs.md)
- [Batch processing reference](../features/batching.md)
- [Batch scheduler rollout](../deployment/batch-scheduler-rollout.md)
- [Batch webhook rollout](../deployment/batch-webhook-rollout.md)
