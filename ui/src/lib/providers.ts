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
  triton: 'NVIDIA Triton',
  lmstudio: 'LM Studio',
  ollama: 'Ollama',
  unknown: 'Unknown',
};

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

export function providerDisplayName(provider: string | null | undefined): string {
  const key = (provider || '').trim().toLowerCase() || 'unknown';
  return PROVIDER_LABELS[key] || key;
}
