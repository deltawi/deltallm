// Emit cached assets so lazy provider images do not grow the initial JS bundle.
import anthropicLogo from '../assets/provider-logos/anthropic.svg?no-inline';
import azureLogo from '../assets/provider-logos/azure.svg?no-inline';
import bedrockLogo from '../assets/provider-logos/bedrock.svg?no-inline';
import deepinfraLogo from '../assets/provider-logos/deepinfra.svg?no-inline';
import deepseekLogo from '../assets/provider-logos/deepseek.svg?no-inline';
import elevenlabsLogo from '../assets/provider-logos/elevenlabs.svg?no-inline';
import fireworksLogo from '../assets/provider-logos/fireworks.svg?no-inline';
import geminiLogo from '../assets/provider-logos/gemini.svg?no-inline';
import groqLogo from '../assets/provider-logos/groq.svg?no-inline';
import lmstudioLogo from '../assets/provider-logos/lmstudio.svg?no-inline';
import minimaxLogo from '../assets/provider-logos/minimax.svg?no-inline';
import ollamaLogo from '../assets/provider-logos/ollama.svg?no-inline';
import openaiLogo from '../assets/provider-logos/openai.svg?no-inline';
import openrouterLogo from '../assets/provider-logos/openrouter.svg?no-inline';
import perplexityLogo from '../assets/provider-logos/perplexity.svg?no-inline';
import qwenLogo from '../assets/provider-logos/qwen.svg?no-inline';
import tencentLogo from '../assets/provider-logos/tencent.svg?no-inline';
import togetherLogo from '../assets/provider-logos/together.svg?no-inline';
import vllmLogo from '../assets/provider-logos/vllm.svg?no-inline';
import zaiLogo from '../assets/provider-logos/zai.svg?no-inline';

export const PROVIDER_LOGOS: Record<string, string> = {
  anthropic: anthropicLogo,
  azure: azureLogo,
  azure_openai: azureLogo,
  bedrock: bedrockLogo,
  deepinfra: deepinfraLogo,
  deepseek: deepseekLogo,
  elevenlabs: elevenlabsLogo,
  fireworks: fireworksLogo,
  gemini: geminiLogo,
  groq: groqLogo,
  lmstudio: lmstudioLogo,
  minimax: minimaxLogo,
  ollama: ollamaLogo,
  openai: openaiLogo,
  openrouter: openrouterLogo,
  perplexity: perplexityLogo,
  qwen: qwenLogo,
  tencent: tencentLogo,
  together: togetherLogo,
  vllm: vllmLogo,
  zai: zaiLogo,
};
