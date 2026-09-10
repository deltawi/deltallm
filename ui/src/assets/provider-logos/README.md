# Provider logos

Local SVG assets shared by provider badges and mobile model cards. Register assets
in `../../lib/providerLogos.ts` with `?no-inline` so Vite emits cached files instead
of embedding SVG markup in the initial JavaScript bundle. Azure and Azure OpenAI
share the same asset. Unknown providers and failed images retain a fallback.

## Added logo sources

These SVGs are copied without modification from [Lobe Icons](https://github.com/lobehub/lobe-icons)
at commit `a94750e3f5f8fc33757b839d85030e742284e43a`. The upstream MIT license and
copyright notice are preserved in [LICENSE.lobe-icons](LICENSE.lobe-icons).
Brand names and marks belong to their respective owners.

| Local file | Upstream source |
| --- | --- |
| [azure.svg](azure.svg) | [azure-color.svg](https://github.com/lobehub/lobe-icons/blob/a94750e3f5f8fc33757b839d85030e742284e43a/packages/static-svg/icons/azure-color.svg) |
| [bedrock.svg](bedrock.svg) | [bedrock-color.svg](https://github.com/lobehub/lobe-icons/blob/a94750e3f5f8fc33757b839d85030e742284e43a/packages/static-svg/icons/bedrock-color.svg) |
| [deepinfra.svg](deepinfra.svg) | [deepinfra-color.svg](https://github.com/lobehub/lobe-icons/blob/a94750e3f5f8fc33757b839d85030e742284e43a/packages/static-svg/icons/deepinfra-color.svg) |
| [deepseek.svg](deepseek.svg) | [deepseek-color.svg](https://github.com/lobehub/lobe-icons/blob/a94750e3f5f8fc33757b839d85030e742284e43a/packages/static-svg/icons/deepseek-color.svg) |
| [lmstudio.svg](lmstudio.svg) | [lmstudio.svg](https://github.com/lobehub/lobe-icons/blob/a94750e3f5f8fc33757b839d85030e742284e43a/packages/static-svg/icons/lmstudio.svg) |
| [minimax.svg](minimax.svg) | [minimax-color.svg](https://github.com/lobehub/lobe-icons/blob/a94750e3f5f8fc33757b839d85030e742284e43a/packages/static-svg/icons/minimax-color.svg) |
| [qwen.svg](qwen.svg) | [qwen-color.svg](https://github.com/lobehub/lobe-icons/blob/a94750e3f5f8fc33757b839d85030e742284e43a/packages/static-svg/icons/qwen-color.svg) |
| [tencent.svg](tencent.svg) | [tencentcloud-color.svg](https://github.com/lobehub/lobe-icons/blob/a94750e3f5f8fc33757b839d85030e742284e43a/packages/static-svg/icons/tencentcloud-color.svg) |
| [together.svg](together.svg) | [together-color.svg](https://github.com/lobehub/lobe-icons/blob/a94750e3f5f8fc33757b839d85030e742284e43a/packages/static-svg/icons/together-color.svg) |
| [zai.svg](zai.svg) | [zai.svg](https://github.com/lobehub/lobe-icons/blob/a94750e3f5f8fc33757b839d85030e742284e43a/packages/static-svg/icons/zai.svg) |

Tencent TokenHub uses the Tencent Cloud mark because [TokenHub is a Tencent Cloud service](https://cloud.tencent.com/product/tokenhub).
