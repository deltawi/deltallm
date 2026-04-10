const PROVIDER_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  azure: 'Azure OpenAI',
  azure_openai: 'Azure OpenAI',
  openrouter: 'OpenRouter',
  groq: 'Groq',
  together: 'Together AI',
  fireworks: 'Fireworks AI',
  deepinfra: 'DeepInfra',
  perplexity: 'Perplexity',
  gemini: 'Google Gemini',
  bedrock: 'AWS Bedrock',
  vllm: 'vLLM',
  lmstudio: 'LM Studio',
  ollama: 'Ollama',
  unknown: 'Unknown',
};

const CUSTOM_UPSTREAM_AUTH_PROVIDERS = new Set([
  'openai',
  'openrouter',
  'groq',
  'together',
  'fireworks',
  'deepinfra',
  'perplexity',
  'vllm',
  'lmstudio',
  'ollama',
]);

export const DEFAULT_CUSTOM_AUTH_HEADER_NAME = 'Authorization';
export const DEFAULT_CUSTOM_AUTH_HEADER_FORMAT = 'Bearer {api_key}';

export function providerFromModelString(model: string | null | undefined): string {
  const value = (model || '').trim();
  if (!value) return 'unknown';
  if (!value.includes('/')) return 'unknown';
  return value.split('/', 1)[0].trim().toLowerCase() || 'unknown';
}

export function normalizeProvider(provider: string | null | undefined, model: string | null | undefined): string {
  const explicit = (provider || '').trim().toLowerCase();
  if (explicit) return explicit;
  return providerFromModelString(model);
}

export function canonicalNamedCredentialProvider(provider: string | null | undefined): string {
  const normalized = (provider || '').trim().toLowerCase();
  return normalized === 'azure' ? 'azure_openai' : normalized;
}

export function providerDisplayName(provider: string | null | undefined): string {
  const key = (provider || '').trim().toLowerCase() || 'unknown';
  return PROVIDER_LABELS[key] || key;
}

export function supportsCustomUpstreamAuthProvider(
  provider: string | null | undefined,
  model?: string | null | undefined,
): boolean {
  return CUSTOM_UPSTREAM_AUTH_PROVIDERS.has(normalizeProvider(provider, model));
}

export function customUpstreamAuthHeaderLabel(
  connectionConfig: Record<string, unknown> | null | undefined,
): string | null {
  const authHeaderName = typeof connectionConfig?.auth_header_name === 'string'
    ? connectionConfig.auth_header_name.trim()
    : '';
  const authHeaderFormat = typeof connectionConfig?.auth_header_format === 'string'
    ? connectionConfig.auth_header_format.trim()
    : '';
  const normalizedHeaderName = authHeaderName || DEFAULT_CUSTOM_AUTH_HEADER_NAME;
  const normalizedHeaderFormat = authHeaderFormat || DEFAULT_CUSTOM_AUTH_HEADER_FORMAT;
  const usesCustomAuth = Boolean(authHeaderName || authHeaderFormat)
    && (
      normalizedHeaderName !== DEFAULT_CUSTOM_AUTH_HEADER_NAME
      || normalizedHeaderFormat !== DEFAULT_CUSTOM_AUTH_HEADER_FORMAT
    );
  return usesCustomAuth ? normalizedHeaderName : null;
}
