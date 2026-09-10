// Emit cached assets so lazy provider images do not grow the initial JS bundle.
import anthropicLogo from '../assets/provider-logos/anthropic.svg?no-inline';
import elevenlabsLogo from '../assets/provider-logos/elevenlabs.svg?no-inline';
import fireworksLogo from '../assets/provider-logos/fireworks.svg?no-inline';
import geminiLogo from '../assets/provider-logos/gemini.svg?no-inline';
import groqLogo from '../assets/provider-logos/groq.svg?no-inline';
import ollamaLogo from '../assets/provider-logos/ollama.svg?no-inline';
import openaiLogo from '../assets/provider-logos/openai.svg?no-inline';
import openrouterLogo from '../assets/provider-logos/openrouter.svg?no-inline';
import perplexityLogo from '../assets/provider-logos/perplexity.svg?no-inline';
import vllmLogo from '../assets/provider-logos/vllm.svg?no-inline';

export const PROVIDER_LOGOS: Record<string, string> = {
  openai: openaiLogo,
  anthropic: anthropicLogo,
  openrouter: openrouterLogo,
  groq: groqLogo,
  fireworks: fireworksLogo,
  perplexity: perplexityLogo,
  gemini: geminiLogo,
  elevenlabs: elevenlabsLogo,
  vllm: vllmLogo,
  ollama: ollamaLogo,
};
