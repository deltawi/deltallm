import assert from 'node:assert/strict';
import test from 'node:test';
import type { ModelDeploymentDetail } from '../src/lib/api';
import { EMPTY_FORM, buildModelPayload, formFromModel, type ModelFormValues } from '../src/components/modelFormShared';

function chatForm(overrides: Partial<ModelFormValues> = {}): ModelFormValues {
  return {
    ...EMPTY_FORM,
    mode: 'chat',
    model_name: 'batch-capable-model',
    provider: 'vllm',
    model: 'meta-llama/Llama-3.1-8B-Instruct',
    api_base: 'https://vllm.example/v1',
    api_key: 'provider-key',
    ...overrides,
  };
}

test('formFromModel loads existing scheduler capacity values', () => {
  const { form } = formFromModel({
    deployment_id: 'dep-1',
    model_name: 'batch-capable-model',
    provider: 'vllm',
    deltallm_params: {
      provider: 'vllm',
      model: 'meta-llama/Llama-3.1-8B-Instruct',
      api_base: 'https://vllm.example/v1',
    },
    model_info: {
      mode: 'embedding',
      batch_capacity: {
        max_in_flight: 4,
        max_claim_work_units: 200,
        capacity_fraction: 0.25,
      },
    },
  } as ModelDeploymentDetail);

  assert.equal(form.mode, 'embedding');
  assert.equal(form.batch_capacity_max_in_flight, '4');
  assert.equal(form.batch_capacity_max_claim_work_units, '200');
  assert.equal(form.batch_capacity_capacity_fraction, '0.25');
});

test('buildModelPayload sets scheduler capacity and preserves unknown model_info fields', () => {
  const payload = buildModelPayload(
    chatForm({
      batch_capacity_max_in_flight: '4',
      batch_capacity_max_claim_work_units: '200',
      batch_capacity_capacity_fraction: '0.25',
      priority: '3',
    }),
    [],
    {
      provider_private_metadata: { region: 'iad' },
      batch_capacity: {
        max_in_flight: 99,
      },
      priority: 1,
    },
  );

  assert.deepEqual(payload.model_info.batch_capacity, {
    max_in_flight: 4,
    max_claim_work_units: 200,
    capacity_fraction: 0.25,
  });
  assert.deepEqual(payload.model_info.provider_private_metadata, { region: 'iad' });
  assert.equal(payload.model_info.priority, 3);
});

test('buildModelPayload preserves existing scheduler capacity on unrelated saves', () => {
  const existingModel: ModelDeploymentDetail = {
    deployment_id: 'dep-1',
    model_name: 'batch-capable-model',
    provider: 'vllm',
    deltallm_params: {
      provider: 'vllm',
      model: 'meta-llama/Llama-3.1-8B-Instruct',
      api_base: 'https://vllm.example/v1',
    },
    model_info: {
      mode: 'chat',
      tags: ['batch'],
      vendor_metadata: { owner: 'infra' },
      batch_capacity: {
        max_in_flight: 4,
        max_claim_work_units: 200,
        capacity_fraction: 0.25,
      },
    },
  };
  const { form, defaultParams } = formFromModel(existingModel);

  const payload = buildModelPayload(
    {
      ...form,
      tags: 'batch, production',
    },
    defaultParams,
    existingModel.model_info,
  );

  assert.deepEqual(payload.model_info.batch_capacity, {
    max_in_flight: 4,
    max_claim_work_units: 200,
    capacity_fraction: 0.25,
  });
  assert.deepEqual(payload.model_info.vendor_metadata, { owner: 'infra' });
  assert.deepEqual(payload.model_info.tags, ['batch', 'production']);
});

test('buildModelPayload clears scheduler capacity when all scheduler fields are blank', () => {
  const payload = buildModelPayload(
    chatForm(),
    [],
    {
      vendor_metadata: { owner: 'infra' },
      batch_capacity: {
        max_in_flight: 4,
        max_claim_work_units: 200,
        capacity_fraction: 0.25,
      },
    },
  );

  assert.equal('batch_capacity' in payload.model_info, false);
  assert.deepEqual(payload.model_info.vendor_metadata, { owner: 'infra' });
});
