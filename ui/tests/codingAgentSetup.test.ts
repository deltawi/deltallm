import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveUiAccess } from '../src/lib/authorization';
import {
  KEY_PLACEHOLDER,
  agentSetup,
  budgetPeriodLabel,
  codingAgentKeyName,
  connectionState,
  currentPeriodSpend,
  defaultAgentModels,
  defaultSmallModel,
  expiryFromDays,
  expiryOptions,
} from '../src/lib/codingAgentSetup';

const ORIGIN = 'https://gateway.acme.dev';
const MODELS = ['claude-haiku-4-5', 'claude-sonnet-4-6', 'gpt-5.2-codex', 'gpt-5-mini'];

test('Claude Code setup uses bearer auth on the gateway origin', () => {
  const setup = agentSetup({ agent: 'claude-code', origin: ORIGIN, apiKey: 'sk-abc', mainModel: 'claude-sonnet-4-6', smallModel: 'claude-haiku-4-5' });
  assert.equal(setup.blocks[0].code, [
    'export ANTHROPIC_BASE_URL="https://gateway.acme.dev"',
    'export ANTHROPIC_AUTH_TOKEN="sk-abc"',
    'export ANTHROPIC_MODEL="claude-sonnet-4-6"',
    'export ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-haiku-4-5"',
  ].join('\n'));
  assert.deepEqual(JSON.parse(setup.alternate!.code), {
    env: {
      ANTHROPIC_BASE_URL: ORIGIN,
      ANTHROPIC_AUTH_TOKEN: 'sk-abc',
      ANTHROPIC_MODEL: 'claude-sonnet-4-6',
      ANTHROPIC_DEFAULT_HAIKU_MODEL: 'claude-haiku-4-5',
    },
  });
});

test('Claude Code setup leaves out models that are not chosen', () => {
  const setup = agentSetup({ agent: 'claude-code', origin: ORIGIN, apiKey: KEY_PLACEHOLDER, mainModel: '', smallModel: '' });
  assert.ok(!setup.blocks[0].code.includes('ANTHROPIC_MODEL'));
  assert.ok(setup.blocks[0].code.includes(KEY_PLACEHOLDER));
});

test('Codex setup adds a DeltaLLM provider on /v1 and reads the key from the environment', () => {
  const setup = agentSetup({ agent: 'codex', origin: ORIGIN, apiKey: 'sk-abc', mainModel: 'gpt-5.2-codex', smallModel: '' });
  const [config, shell] = setup.blocks;
  assert.match(config.code, /^model = "gpt-5\.2-codex"$/m);
  assert.match(config.code, /^base_url = "https:\/\/gateway\.acme\.dev\/v1"$/m);
  assert.match(config.code, /^env_key = "DELTALLM_API_KEY"$/m);
  assert.ok(!config.code.includes('sk-abc'));
  assert.equal(shell.code, 'export DELTALLM_API_KEY="sk-abc"');
});

test('OpenCode setup lists the chosen models under the provider', () => {
  const setup = agentSetup({ agent: 'opencode', origin: ORIGIN, apiKey: 'sk-abc', mainModel: 'claude-sonnet-4-6', smallModel: 'claude-haiku-4-5' });
  const config = JSON.parse(setup.blocks[0].code);
  assert.equal(config.provider.deltallm.options.baseURL, `${ORIGIN}/v1`);
  assert.equal(config.provider.deltallm.options.apiKey, '{env:DELTALLM_API_KEY}');
  assert.deepEqual(Object.keys(config.provider.deltallm.models), ['claude-sonnet-4-6', 'claude-haiku-4-5']);
  assert.equal(config.model, 'deltallm/claude-sonnet-4-6');
  assert.equal(config.small_model, 'deltallm/claude-haiku-4-5');
});

test('shell values are quoted so a key cannot break the line', () => {
  const setup = agentSetup({ agent: 'other', origin: ORIGIN, apiKey: 'sk-"$x', mainModel: '', smallModel: '' });
  assert.ok(setup.blocks[0].code.includes('export OPENAI_API_KEY="sk-\\"\\$x"'));
});

test('default models follow the agent', () => {
  assert.deepEqual(defaultAgentModels('claude-code', MODELS), { main: 'claude-sonnet-4-6', small: 'claude-haiku-4-5' });
  assert.deepEqual(defaultAgentModels('codex', MODELS), { main: 'gpt-5.2-codex', small: 'gpt-5-mini' });
  assert.deepEqual(defaultAgentModels('claude-code', ['llama-3']), { main: 'llama-3', small: '' });
  assert.deepEqual(defaultAgentModels('other', []), { main: '', small: '' });
  assert.equal(defaultSmallModel('claude-code', MODELS, 'claude-sonnet-4-6'), 'claude-haiku-4-5');
  assert.equal(defaultSmallModel('claude-code', MODELS, 'claude-haiku-4-5'), '');
});

test('spend of an ended period shows as zero until the gateway resets it', () => {
  const now = Date.parse('2026-10-17T12:00:00Z');
  assert.equal(currentPeriodSpend({ spend: 4.5, budget_duration: '1d', budget_reset_at: '2026-10-18T00:00:00+00:00' }, now), 4.5);
  assert.equal(currentPeriodSpend({ spend: 4.5, budget_duration: '1d', budget_reset_at: '2026-10-17T00:00:00+00:00' }, now), 0);
  assert.equal(currentPeriodSpend({ spend: 4.5, budget_duration: null, budget_reset_at: null }, now), 4.5);
});

test('expiry choices respect the team policy', () => {
  assert.deepEqual(expiryOptions().map((option) => option.label), ['30 days', '90 days', '1 year', 'Never']);
  assert.deepEqual(expiryOptions({ maxDays: 60, required: true }).map((option) => option.label), ['30 days', '60 days']);
  assert.deepEqual(expiryOptions({ required: true }).map((option) => option.label), ['30 days', '90 days', '1 year']);
  assert.deepEqual(expiryOptions({ maxDays: 90 }).map((option) => option.days), [30, 90]);
});

test('expiry stays inside the chosen number of days', () => {
  const now = new Date('2026-10-17T12:00:00Z');
  const expires = Date.parse(expiryFromDays(90, now)!);
  assert.ok(expires < now.getTime() + 90 * 86_400_000);
  assert.ok(expires > now.getTime() + 89 * 86_400_000);
  assert.equal(expiryFromDays(null, now), undefined);
});

test('labels for key names, periods and connection', () => {
  assert.equal(codingAgentKeyName('sara@acme.dev', 'claude-code'), 'sara@acme.dev · Claude Code');
  assert.equal(budgetPeriodLabel('1d'), 'day');
  assert.equal(budgetPeriodLabel('7d'), '7 days');
  assert.equal(connectionState(null).connected, false);
  const now = Date.parse('2026-10-17T12:00:00Z');
  assert.deepEqual(connectionState('2026-10-17T11:55:00Z', now), { connected: true, label: 'Connected · last request 5 min ago' });
});

test('the coding agents page needs a way to create keys', () => {
  const session = (permissions: string[]) => ({ authenticated: true, role: 'org_user', effective_permissions: permissions });
  assert.equal(resolveUiAccess('session', session(['key.update'])).coding_agents, true);
  assert.equal(resolveUiAccess('session', session(['key.create_self'])).coding_agents, true);
  assert.equal(resolveUiAccess('session', session(['key.read'])).coding_agents, false);
  assert.equal(resolveUiAccess('master_key', null).coding_agents, true);
});
