import assert from 'node:assert/strict';
import test from 'node:test';

import { parseFallbackEvents } from '../src/lib/fallbackEvents';

test('fallback events preserve a null source for local context selection', () => {
  assert.deepEqual(parseFallbackEvents({
    events: [{
      timestamp: 123,
      model_group: 'support',
      from_deployment: null,
      to_deployment: 'dep-large',
      error_classification: 'context_window_exceeded',
      success: true,
    }],
  }), [{
    timestamp: 123,
    model_group: 'support',
    from_deployment: null,
    to_deployment: 'dep-large',
    error_classification: 'context_window_exceeded',
    success: true,
  }]);
});

test('fallback event parsing rejects malformed deployment identifiers', () => {
  assert.deepEqual(parseFallbackEvents({
    events: [{
      timestamp: 123,
      model_group: 'support',
      from_deployment: 42,
      to_deployment: 'dep-large',
      error_classification: 'context_window_exceeded',
      success: true,
    }],
  }), []);
});
