import assert from 'node:assert/strict';
import test from 'node:test';
import type { AuthSsoConfig, ModelDeploymentDetail, Principal } from '../src/lib/api';
import {
  EMPTY_FORM,
  buildModelPayload,
  formFromModel,
  parseOptionalPositiveInteger,
  validateContextCapacityFields,
  type ModelFormValues,
} from '../src/components/modelFormShared';
import {
  formatOptionalBudget,
  formatOptionalLimit,
  hasSandboxSelfRegistration,
  isSelfRegisteredPrincipal,
} from '../src/lib/selfRegistration';

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

test('context capacity parsing distinguishes blank, valid, and invalid values', () => {
  assert.deepEqual(parseOptionalPositiveInteger('  '), { kind: 'blank' });
  assert.deepEqual(parseOptionalPositiveInteger('128000'), { kind: 'valid', value: 128000 });
  for (const invalid of ['0', '-1', '1.5', 'not-a-number', '9007199254740992']) {
    assert.deepEqual(parseOptionalPositiveInteger(invalid), { kind: 'invalid' });
  }
});

test('context capacity validation is mode-aware', () => {
  assert.deepEqual(validateContextCapacityFields(chatForm({
    max_context_window: '0',
    max_input_tokens: '-1',
    max_output_tokens: '1.5',
  })), {
    max_context_window: 'Context Window must be a whole number greater than or equal to 1.',
    max_input_tokens: 'Max Input Tokens must be a whole number greater than or equal to 1.',
    max_output_tokens: 'Max Output Tokens must be a whole number greater than or equal to 1.',
  });
  assert.deepEqual(validateContextCapacityFields(chatForm({
    mode: 'embedding',
    max_context_window: '8192',
    max_input_tokens: '-1',
    max_output_tokens: '-1',
  })), {
    max_input_tokens: 'Max Input Tokens must be a whole number greater than or equal to 1.',
  });
});

test('embedding edits preserve configured max input capacity', () => {
  const payload = buildModelPayload(
    chatForm({
      mode: 'embedding',
      max_context_window: '8192',
      max_input_tokens: '8000',
    }),
    [],
    {
      max_tokens: 8192,
      max_input_tokens: 8000,
      provider_private_metadata: { region: 'iad' },
    },
  );
  const serializedModelInfo = JSON.parse(JSON.stringify(payload.model_info)) as Record<string, unknown>;

  assert.equal(serializedModelInfo.max_tokens, 8192);
  assert.equal(serializedModelInfo.max_input_tokens, 8000);
  assert.deepEqual(serializedModelInfo.provider_private_metadata, { region: 'iad' });
});

test('invalid nonblank context capacity cannot erase existing model metadata', () => {
  assert.throws(
    () => buildModelPayload(
      chatForm({ max_context_window: '0' }),
      [],
      { max_tokens: 128000, provider_private_metadata: { region: 'iad' } },
    ),
    /Context Window must be a positive safe integer/,
  );
});

test('blank context capacity intentionally clears existing metadata', () => {
  const payload = buildModelPayload(
    chatForm({ max_context_window: '', max_input_tokens: '', max_output_tokens: '' }),
    [],
    { max_tokens: 128000, max_input_tokens: 120000, max_output_tokens: 8000 },
  );
  const serializedModelInfo = JSON.parse(JSON.stringify(payload.model_info)) as Record<string, unknown>;

  assert.equal('max_tokens' in serializedModelInfo, false);
  assert.equal('max_input_tokens' in serializedModelInfo, false);
  assert.equal('max_output_tokens' in serializedModelInfo, false);
});

test('legacy non-positive context capacity loads as unknown and is removed on edit', () => {
  const existingModel = {
    deployment_id: 'dep-legacy-capacity',
    model_name: 'legacy-capacity-model',
    provider: 'vllm',
    deltallm_params: {
      provider: 'vllm',
      model: 'meta-llama/Llama-3.1-8B-Instruct',
      api_base: 'https://vllm.example/v1',
    },
    model_info: {
      mode: 'chat',
      max_tokens: 0,
      max_input_tokens: -1,
      max_output_tokens: 0,
      provider_private_metadata: { region: 'iad' },
    },
  } as ModelDeploymentDetail;

  const { form, defaultParams } = formFromModel(existingModel);
  const payload = buildModelPayload(form, defaultParams, existingModel.model_info);
  const serializedModelInfo = JSON.parse(JSON.stringify(payload.model_info)) as Record<string, unknown>;

  assert.equal(form.max_context_window, '');
  assert.equal(form.max_input_tokens, '');
  assert.equal(form.max_output_tokens, '');
  assert.equal('max_tokens' in serializedModelInfo, false);
  assert.equal('max_input_tokens' in serializedModelInfo, false);
  assert.equal('max_output_tokens' in serializedModelInfo, false);
  assert.deepEqual(serializedModelInfo.provider_private_metadata, { region: 'iad' });
});

test('legacy digit-string context capacity is normalized on edit', () => {
  const existingModel = {
    deployment_id: 'dep-legacy-string-capacity',
    model_name: 'legacy-capacity-model',
    provider: 'vllm',
    deltallm_params: {
      provider: 'vllm',
      model: 'meta-llama/Llama-3.1-8B-Instruct',
    },
    model_info: {
      mode: 'chat',
      max_tokens: '8192',
      max_input_tokens: '8000',
    },
  } as ModelDeploymentDetail;

  const { form, defaultParams } = formFromModel(existingModel);
  const payload = buildModelPayload(form, defaultParams, existingModel.model_info);

  assert.equal(form.max_context_window, '8192');
  assert.equal(form.max_input_tokens, '8000');
  assert.equal(payload.model_info.max_tokens, 8192);
  assert.equal(payload.model_info.max_input_tokens, 8000);
});

test('self-registration helpers require enabled SSO sandbox access', () => {
  const enabled: AuthSsoConfig = {
    sso_enabled: true,
    provider: 'oidc',
    self_registration: {
      enabled: true,
      mode: 'sso_allowed_domain',
      sandbox_access_enabled: true,
    },
  };

  assert.equal(hasSandboxSelfRegistration(enabled), true);
  assert.equal(hasSandboxSelfRegistration({ ...enabled, sso_enabled: false }), false);
  assert.equal(
    hasSandboxSelfRegistration({
      ...enabled,
      self_registration: { ...enabled.self_registration!, sandbox_access_enabled: false },
    }),
    false,
  );
});

test('self-registration helpers identify principals and format optional limits', () => {
  const principal = {
    self_registration: { is_self_registered: true },
  } as Principal;

  assert.equal(isSelfRegisteredPrincipal(principal), true);
  assert.equal(isSelfRegisteredPrincipal(null), false);
  assert.equal(formatOptionalLimit(50000), '50,000');
  assert.equal(formatOptionalLimit(null), 'No limit');
  assert.equal(formatOptionalBudget(5), '$5');
  assert.equal(formatOptionalBudget(null), 'No limit');
});
