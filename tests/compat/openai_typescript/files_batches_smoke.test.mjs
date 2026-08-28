import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import OpenAI, { toFile } from "openai";


const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const fixtureDirectory = path.resolve(currentDirectory, "../../contracts/fixtures");
const httpFixture = JSON.parse(
  await readFile(path.join(fixtureDirectory, "files_batches_http.json"), "utf8"),
);
const inputBytes = await readFile(path.join(fixtureDirectory, "batch_input.jsonl"));
const outputBytes = await readFile(path.join(fixtureDirectory, "batch_output.jsonl"));


function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "X-Request-Id": "req_compat_typescript_001",
    },
  });
}


function buildFixtureFetch(observed) {
  return async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = new URL(request.url);
    const route = `${request.method} ${url.pathname}`;
    // The SDK probes custom fetch implementations with a non-API request.
    if (route === "GET ,") {
      return new Response(null, { status: 204 });
    }
    observed.routes.push(route);

    if (route === "POST /v1/files") {
      const form = await request.clone().formData();
      const file = form.get("file");
      observed.uploadPurpose = form.get("purpose");
      observed.uploadFilename = file?.name;
      observed.uploadBytes = file ? Buffer.from(await file.arrayBuffer()) : null;
      return jsonResponse(httpFixture.file_retrieve.response.body);
    }
    if (route === "GET /v1/files/file_input_001") {
      return jsonResponse(httpFixture.file_retrieve.response.body);
    }
    if (route === "GET /v1/files/file_output_001/content") {
      return new Response(outputBytes, {
        status: 200,
        headers: { "Content-Type": "application/jsonl" },
      });
    }
    if (route === "POST /v1/batches") {
      observed.batchCreateBody = await request.clone().json();
      observed.idempotencyKey = request.headers.get("Idempotency-Key");
      return jsonResponse(httpFixture.batch_create.response.body);
    }
    if (route === "GET /v1/batches/batch_completed_001") {
      return jsonResponse(httpFixture.batch_retrieve.response.body);
    }
    if (route === "GET /v1/batches") {
      return jsonResponse(httpFixture.batch_list.response.body);
    }
    if (route === "POST /v1/batches/batch_completed_001/cancel") {
      return jsonResponse(httpFixture.batch_cancel.response.body);
    }
    if (route === "GET /v1/files") {
      return jsonResponse({ detail: "Method Not Allowed" }, 405);
    }
    throw new Error(`unexpected compatibility request: ${route}`);
  };
}


test("official OpenAI TypeScript client supports upload and basic batch calls", async () => {
  const observed = { routes: [] };
  const client = new OpenAI({
    apiKey: "sk-compat-fixture",
    baseURL: "http://deltallm.test/v1",
    fetch: buildFixtureFetch(observed),
    maxRetries: 0,
  });

  const uploaded = await client.files.create({
    file: await toFile(inputBytes, "batch_input.jsonl"),
    purpose: "batch",
  });
  const created = await client.batches.create(
    {
      input_file_id: uploaded.id,
      endpoint: "/v1/embeddings",
      completion_window: "24h",
      metadata: { customer_job_id: "job-42" },
    },
    { headers: { "Idempotency-Key": "compat-typescript-001" } },
  );
  const retrievedFile = await client.files.retrieve(uploaded.id);
  const retrievedBatch = await client.batches.retrieve("batch_completed_001");
  const page = await client.batches.list({ limit: 20 });
  const cancelled = await client.batches.cancel("batch_completed_001");
  const content = await client.files.content("file_output_001");

  assert.equal(uploaded.id, "file_input_001");
  assert.equal(retrievedFile.purpose, "batch");
  assert.equal(created.status, "validating");
  assert.equal(retrievedBatch.status, "completed");
  assert.equal(retrievedBatch.request_counts.completed, 2);
  assert.deepEqual(page.data.map((batch) => batch.id), ["batch_completed_001"]);
  assert.equal(cancelled.status, "cancelling");
  assert.deepEqual(Buffer.from(await content.arrayBuffer()), outputBytes);
  assert.equal(observed.uploadPurpose, "batch");
  assert.equal(observed.uploadFilename, "batch_input.jsonl");
  assert.deepEqual(observed.uploadBytes, inputBytes);
  assert.equal(observed.idempotencyKey, "compat-typescript-001");
  assert.deepEqual(observed.batchCreateBody, {
    input_file_id: "file_input_001",
    endpoint: "/v1/embeddings",
    completion_window: "24h",
    metadata: { customer_job_id: "job-42" },
  });
  assert.deepEqual(observed.routes, [
    "POST /v1/files",
    "POST /v1/batches",
    "GET /v1/files/file_input_001",
    "GET /v1/batches/batch_completed_001",
    "GET /v1/batches",
    "POST /v1/batches/batch_completed_001/cancel",
    "GET /v1/files/file_output_001/content",
  ]);
});


test.todo("known gap: GET /v1/files is not implemented (issue #280 Slice 4)");
test.todo(
  "known gap: batch pages omit has_more and ignore after cursors (issue #280 Slice 3)",
);
