import type { ApiKey, CodingAgentTag } from './api';
import { keyLastUsedDisplay } from './apiKeyLastUsed';

export type CodingAgentId = CodingAgentTag['agent'];

export type CodingAgentInfo = {
  id: CodingAgentId;
  label: string;
  // Words a model name usually contains, best first, for the default picks.
  mainHints: string[];
  smallHints: string[];
  color: string;
};

export const CODING_AGENTS: readonly CodingAgentInfo[] = [
  { id: 'claude-code', label: 'Claude Code', mainHints: ['sonnet', 'opus', 'claude'], smallHints: ['haiku'], color: '#D97757' },
  { id: 'codex', label: 'Codex', mainHints: ['codex', 'gpt-5', 'gpt'], smallHints: ['mini', 'nano'], color: '#10A37F' },
  { id: 'opencode', label: 'OpenCode', mainHints: ['sonnet', 'gpt-5', 'claude', 'gpt'], smallHints: ['haiku', 'mini'], color: '#3B82F6' },
  { id: 'other', label: 'Other', mainHints: [], smallHints: [], color: '#6B7280' },
];

// Placeholder for setups opened after creation, when the key is no longer shown.
export const KEY_PLACEHOLDER = 'YOUR_DELTALLM_KEY';

export function codingAgentInfo(id: string | null | undefined): CodingAgentInfo {
  return CODING_AGENTS.find((agent) => agent.id === id) ?? CODING_AGENTS[CODING_AGENTS.length - 1];
}

function pickModel(models: readonly string[], hints: readonly string[], exclude?: string): string {
  const candidates = models.filter((model) => model !== exclude);
  for (const hint of hints) {
    const match = candidates.find((model) => model.toLowerCase().includes(hint));
    if (match) return match;
  }
  return '';
}

/** Default main and small models for an agent from the models the team can use. */
export function defaultAgentModels(agent: CodingAgentId, models: readonly string[]): { main: string; small: string } {
  const main = pickModel(models, codingAgentInfo(agent).mainHints) || models[0] || '';
  return { main, small: defaultSmallModel(agent, models, main) };
}

/** Default small model next to a chosen main model. */
export function defaultSmallModel(agent: CodingAgentId, models: readonly string[], main: string): string {
  return pickModel(models, codingAgentInfo(agent).smallHints, main);
}

/** The gateway origin. Agents add their own API paths, such as /v1/messages. */
export function gatewayOrigin(): string {
  return typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000';
}

export type SetupBlock = {
  id: string;
  // Where the text goes, such as "Shell profile" or "~/.codex/config.toml".
  title: string;
  code: string;
};

export type AgentSetup = {
  intro: string;
  blocks: SetupBlock[];
  // Another format of the same setup, offered as a second copy button.
  alternate?: SetupBlock;
};

export type SetupInput = {
  agent: CodingAgentId;
  origin: string;
  apiKey: string;
  mainModel: string;
  smallModel: string;
};

function shellQuote(value: string): string {
  return `"${value.replace(/(["\\$`])/g, '\\$1')}"`;
}

function claudeCodeSetup({ origin, apiKey, mainModel, smallModel }: SetupInput): AgentSetup {
  // ANTHROPIC_AUTH_TOKEN sends "Authorization: Bearer", which every gateway route accepts.
  const env: Array<[string, string]> = [
    ['ANTHROPIC_BASE_URL', origin],
    ['ANTHROPIC_AUTH_TOKEN', apiKey],
  ];
  if (mainModel) env.push(['ANTHROPIC_MODEL', mainModel]);
  if (smallModel) env.push(['ANTHROPIC_DEFAULT_HAIKU_MODEL', smallModel]);
  return {
    intro: 'Add these lines to the shell profile (for example ~/.zshrc), open a new terminal, then run claude.',
    blocks: [{
      id: 'shell',
      title: 'Shell profile',
      code: env.map(([name, value]) => `export ${name}=${shellQuote(value)}`).join('\n'),
    }],
    alternate: {
      id: 'settings',
      title: '~/.claude/settings.json',
      code: JSON.stringify({ env: Object.fromEntries(env) }, null, 2),
    },
  };
}

function codexSetup({ origin, apiKey, mainModel }: SetupInput): AgentSetup {
  const lines = [
    ...(mainModel ? [`model = "${mainModel}"`] : []),
    'model_provider = "deltallm"',
    '',
    '[model_providers.deltallm]',
    'name = "DeltaLLM"',
    `base_url = "${origin}/v1"`,
    'env_key = "DELTALLM_API_KEY"',
    'wire_api = "responses"',
  ];
  return {
    intro: 'Add the provider to the Codex config file and the key to the shell profile, then run codex.',
    blocks: [
      { id: 'config', title: '~/.codex/config.toml', code: lines.join('\n') },
      { id: 'shell', title: 'Shell profile', code: `export DELTALLM_API_KEY=${shellQuote(apiKey)}` },
    ],
  };
}

function openCodeSetup({ origin, apiKey, mainModel, smallModel }: SetupInput): AgentSetup {
  const models = [mainModel, smallModel].filter(Boolean);
  const config: Record<string, unknown> = {
    $schema: 'https://opencode.ai/config.json',
    provider: {
      deltallm: {
        npm: '@ai-sdk/openai-compatible',
        name: 'DeltaLLM',
        options: { baseURL: `${origin}/v1`, apiKey: '{env:DELTALLM_API_KEY}' },
        models: Object.fromEntries(models.map((model) => [model, { name: model }])),
      },
    },
  };
  if (mainModel) config.model = `deltallm/${mainModel}`;
  if (smallModel) config.small_model = `deltallm/${smallModel}`;
  return {
    intro: 'Add the provider to opencode.json in the project or in ~/.config/opencode, and the key to the shell profile.',
    blocks: [
      { id: 'config', title: 'opencode.json', code: JSON.stringify(config, null, 2) },
      { id: 'shell', title: 'Shell profile', code: `export DELTALLM_API_KEY=${shellQuote(apiKey)}` },
    ],
  };
}

function otherSetup({ origin, apiKey, mainModel }: SetupInput): AgentSetup {
  const lines = [
    '# OpenAI-compatible tools',
    `export OPENAI_BASE_URL=${shellQuote(`${origin}/v1`)}`,
    `export OPENAI_API_KEY=${shellQuote(apiKey)}`,
    '',
    '# Anthropic-compatible tools',
    `export ANTHROPIC_BASE_URL=${shellQuote(origin)}`,
    `export ANTHROPIC_AUTH_TOKEN=${shellQuote(apiKey)}`,
  ];
  return {
    intro: mainModel
      ? `Most agents read one of these sets of variables. Select the model ${mainModel} in the agent.`
      : 'Most agents read one of these sets of variables.',
    blocks: [{ id: 'shell', title: 'Shell profile', code: lines.join('\n') }],
  };
}

export function agentSetup(input: SetupInput): AgentSetup {
  switch (input.agent) {
    case 'claude-code':
      return claudeCodeSetup(input);
    case 'codex':
      return codexSetup(input);
    case 'opencode':
      return openCodeSetup(input);
    default:
      return otherSetup(input);
  }
}

/** Spend in the current budget period. A period that ended resets on the next request. */
export function currentPeriodSpend(key: Pick<ApiKey, 'spend' | 'budget_duration' | 'budget_reset_at'>, now = Date.now()): number {
  const spend = Number(key.spend) || 0;
  if (!key.budget_duration || !key.budget_reset_at) return spend;
  const resetAt = Date.parse(key.budget_reset_at);
  return Number.isFinite(resetAt) && resetAt <= now ? 0 : spend;
}

export function budgetPeriodLabel(duration: string | null | undefined): string {
  if (!duration) return 'total';
  const match = /^(\d+)(h|d|mo)$/.exec(duration);
  if (!match) return duration;
  const amount = Number(match[1]);
  const unit = { h: 'hour', d: 'day', mo: 'month' }[match[2] as 'h' | 'd' | 'mo'];
  return amount === 1 ? unit : `${amount} ${unit}s`;
}

export type ExpiryOption = { days: number | null; label: string };

const EXPIRY_OPTIONS: readonly ExpiryOption[] = [
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
  { days: 365, label: '1 year' },
  { days: null, label: 'Never' },
];

/** Expiry choices allowed by a team policy. A team maximum adds itself as the last dated choice. */
export function expiryOptions(policy?: { maxDays?: number | null; required?: boolean }): ExpiryOption[] {
  const maxDays = policy?.maxDays ?? null;
  const options = EXPIRY_OPTIONS.filter((option) => {
    if (option.days === null) return !policy?.required && maxDays === null;
    return maxDays === null || option.days <= maxDays;
  });
  if (maxDays !== null && !options.some((option) => option.days === maxDays)) {
    options.push({ days: maxDays, label: `${maxDays} days` });
    options.sort((a, b) => (a.days ?? Infinity) - (b.days ?? Infinity));
  }
  return options;
}

export function expiryFromDays(days: number | null, now = new Date()): string | undefined {
  if (days === null) return undefined;
  // A few minutes of margin keeps a "90 days" choice inside a 90-day team maximum.
  return new Date(now.getTime() + days * 86_400_000 - 5 * 60_000).toISOString();
}

export function codingAgentKeyName(developer: string, agent: CodingAgentId): string {
  return `${developer} · ${codingAgentInfo(agent).label}`;
}

export type ConnectionState = { connected: boolean; label: string };

export function connectionState(lastUsedAt: string | null | undefined, now = Date.now()): ConnectionState {
  const display = keyLastUsedDisplay(lastUsedAt, now);
  if (!display.title) return { connected: false, label: 'Waiting for the first request' };
  return { connected: true, label: `Connected · last request ${display.label.toLowerCase()}` };
}
