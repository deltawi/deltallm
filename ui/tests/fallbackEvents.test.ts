import assert from 'node:assert/strict';
import test from 'node:test';

import { fallbackTargetLabel, parseFallbackEvents } from '../src/lib/fallbackEvents';

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


test('failed attempts without a transition are labeled clearly', () => {
  assert.equal(fallbackTargetLabel({
    timestamp: 123,
    model_group: 'support',
    from_deployment: 'dep-primary',
    to_deployment: null,
    error_classification: 'generic',
    success: false,
  }), 'Attempt failed');
});
