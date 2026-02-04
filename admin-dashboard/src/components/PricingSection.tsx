import { useState } from 'react';
import { ChevronDown, ChevronRight, DollarSign, Tag } from 'lucide-react';
import type { PricingCreateRequest } from '@/types';

interface PricingSectionProps {
  modelType: string;
  pricingData: PricingCreateRequest | null;
  onChange: (data: PricingCreateRequest | null) => void;
  hasCustomPricing?: boolean;
}

const MODEL_TYPE_TO_PRICING_MODE: Record<string, PricingCreateRequest['mode']> = {
  chat: 'chat',
  embedding: 'embedding',
  image_generation: 'image_generation',
  audio_transcription: 'audio_transcription',
  audio_speech: 'audio_speech',
  rerank: 'rerank',
  moderation: 'moderation',
};

export function PricingSection({
  modelType,
  pricingData,
  onChange,
  hasCustomPricing = false,
}: PricingSectionProps) {
  const [isExpanded, setIsExpanded] = useState(!!pricingData);
  const [enablePricing, setEnablePricing] = useState(!!pricingData);

  const mode = MODEL_TYPE_TO_PRICING_MODE[modelType] || 'chat';

  const handleEnablePricingChange = (enabled: boolean) => {
    setEnablePricing(enabled);
    if (enabled) {
      onChange({
        mode,
        input_cost_per_token: '',
        output_cost_per_token: '',
      });
    } else {
      onChange(null);
    }
  };

  const updateField = (field: keyof PricingCreateRequest, value: string | number | undefined) => {
    if (!pricingData) return;
    onChange({
      ...pricingData,
      mode,
      [field]: value,
    });
  };

  const renderChatFields = () => (
    <>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Input Cost (per 1M tokens)
          </label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
            <input
              type="text"
              value={pricingData?.input_cost_per_token || ''}
              onChange={(e) => updateField('input_cost_per_token', e.target.value)}
              placeholder="2.50"
              className="w-full pl-7 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            />
          </div>
          <p className="text-xs text-gray-500 mt-1">e.g., 2.50 for $2.50 per million tokens</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Output Cost (per 1M tokens)
          </label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
            <input
              type="text"
              value={pricingData?.output_cost_per_token || ''}
              onChange={(e) => updateField('output_cost_per_token', e.target.value)}
              placeholder="10.00"
              className="w-full pl-7 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            />
          </div>
          <p className="text-xs text-gray-500 mt-1">e.g., 10.00 for $10 per million tokens</p>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Cache Creation Cost (per 1M tokens)
          </label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
            <input
              type="text"
              value={pricingData?.cache_creation_input_token_cost || ''}
              onChange={(e) => updateField('cache_creation_input_token_cost', e.target.value)}
              placeholder="Optional"
              className="w-full pl-7 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            />
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Cache Read Cost (per 1M tokens)
          </label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
            <input
              type="text"
              value={pricingData?.cache_read_input_token_cost || ''}
              onChange={(e) => updateField('cache_read_input_token_cost', e.target.value)}
              placeholder="Optional"
              className="w-full pl-7 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            />
          </div>
        </div>
      </div>
    </>
  );

  const renderEmbeddingFields = () => (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">
        Input Cost (per 1M tokens)
      </label>
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
        <input
          type="text"
          value={pricingData?.input_cost_per_token || ''}
          onChange={(e) => updateField('input_cost_per_token', e.target.value)}
          placeholder="0.10"
          className="w-full pl-7 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
        />
      </div>
      <p className="text-xs text-gray-500 mt-1">e.g., 0.10 for $0.10 per million tokens</p>
    </div>
  );

  const renderImageFields = () => (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">
        Cost per Image
      </label>
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
        <input
          type="text"
          value={pricingData?.image_cost_per_image || ''}
          onChange={(e) => updateField('image_cost_per_image', e.target.value)}
          placeholder="0.02"
          className="w-full pl-7 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
        />
      </div>
      <p className="text-xs text-gray-500 mt-1">Cost per generated image (e.g., 0.02 for $0.02)</p>
    </div>
  );

  const renderAudioTranscriptionFields = () => (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">
        Cost per Minute
      </label>
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
        <input
          type="text"
          value={pricingData?.audio_cost_per_minute || ''}
          onChange={(e) => updateField('audio_cost_per_minute', e.target.value)}
          placeholder="0.006"
          className="w-full pl-7 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
        />
      </div>
      <p className="text-xs text-gray-500 mt-1">Cost per minute of transcribed audio</p>
    </div>
  );

  const renderAudioSpeechFields = () => (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">
        Cost per 1K Characters
      </label>
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
        <input
          type="text"
          value={pricingData?.audio_cost_per_character || ''}
          onChange={(e) => updateField('audio_cost_per_character', e.target.value)}
          placeholder="0.015"
          className="w-full pl-7 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
        />
      </div>
      <p className="text-xs text-gray-500 mt-1">Cost per 1,000 characters of generated speech</p>
    </div>
  );

  const renderRerankFields = () => (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">
        Cost per Search
      </label>
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">$</span>
        <input
          type="text"
          value={pricingData?.rerank_cost_per_search || ''}
          onChange={(e) => updateField('rerank_cost_per_search', e.target.value)}
          placeholder="0.001"
          className="w-full pl-7 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
        />
      </div>
      <p className="text-xs text-gray-500 mt-1">Cost per reranking operation</p>
    </div>
  );

  const renderFieldsForModelType = () => {
    switch (modelType) {
      case 'chat':
        return renderChatFields();
      case 'embedding':
        return renderEmbeddingFields();
      case 'image_generation':
        return renderImageFields();
      case 'audio_transcription':
        return renderAudioTranscriptionFields();
      case 'audio_speech':
        return renderAudioSpeechFields();
      case 'rerank':
        return renderRerankFields();
      default:
        return renderChatFields();
    }
  };

  return (
    <div className="border-t border-gray-200 pt-4">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center justify-between w-full text-left"
      >
        <div className="flex items-center">
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-gray-500 mr-2" />
          ) : (
            <ChevronRight className="w-4 h-4 text-gray-500 mr-2" />
          )}
          <DollarSign className="w-4 h-4 text-gray-500 mr-2" />
          <span className="text-sm font-medium text-gray-700">Pricing Configuration</span>
          {hasCustomPricing && (
            <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">
              <Tag className="w-3 h-3 mr-1" />
              Custom
            </span>
          )}
        </div>
        <span className="text-xs text-gray-500">
          {enablePricing ? 'Custom pricing enabled' : 'Using default pricing'}
        </span>
      </button>

      {isExpanded && (
        <div className="mt-4 space-y-4">
          <div className="flex items-center">
            <input
              type="checkbox"
              id="enable_pricing"
              checked={enablePricing}
              onChange={(e) => handleEnablePricingChange(e.target.checked)}
              className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
            />
            <label htmlFor="enable_pricing" className="ml-2 text-sm text-gray-700">
              Set custom pricing for this model
            </label>
          </div>

          {enablePricing && pricingData && (
            <div className="space-y-4 pl-6 border-l-2 border-gray-200">
              {renderFieldsForModelType()}

              {/* Token Limits - Always shown */}
              <div className="border-t border-gray-100 pt-4">
                <h5 className="text-sm font-medium text-gray-700 mb-3">Token Limits (Optional)</h5>
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">
                      Max Tokens
                    </label>
                    <input
                      type="number"
                      min={0}
                      value={pricingData.max_tokens || ''}
                      onChange={(e) =>
                        updateField('max_tokens', e.target.value ? parseInt(e.target.value) : undefined)
                      }
                      placeholder="Default"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">
                      Max Input Tokens
                    </label>
                    <input
                      type="number"
                      min={0}
                      value={pricingData.max_input_tokens || ''}
                      onChange={(e) =>
                        updateField('max_input_tokens', e.target.value ? parseInt(e.target.value) : undefined)
                      }
                      placeholder="Default"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">
                      Max Output Tokens
                    </label>
                    <input
                      type="number"
                      min={0}
                      value={pricingData.max_output_tokens || ''}
                      onChange={(e) =>
                        updateField('max_output_tokens', e.target.value ? parseInt(e.target.value) : undefined)
                      }
                      placeholder="Default"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none text-sm"
                    />
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
