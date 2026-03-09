import anthropicLogo from '../assets/provider-logos/anthropic.svg';
import fireworksLogo from '../assets/provider-logos/fireworks.svg';
import geminiLogo from '../assets/provider-logos/gemini.svg';
import groqLogo from '../assets/provider-logos/groq.svg';
import ollamaLogo from '../assets/provider-logos/ollama.svg';
import openaiLogo from '../assets/provider-logos/openai.svg';
import openrouterLogo from '../assets/provider-logos/openrouter.svg';
import perplexityLogo from '../assets/provider-logos/perplexity.svg';
import vllmLogo from '../assets/provider-logos/vllm.svg';

export const PROVIDER_LOGOS: Record<string, string> = {
  openai: openaiLogo,
  anthropic: anthropicLogo,
  openrouter: openrouterLogo,
  groq: groqLogo,
  fireworks: fireworksLogo,
  perplexity: perplexityLogo,
  gemini: geminiLogo,
  vllm: vllmLogo,
  ollama: ollamaLogo,
};
