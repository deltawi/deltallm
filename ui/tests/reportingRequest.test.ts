import assert from 'node:assert/strict';
import test from 'node:test';

import { reportingRequestInit } from '../src/lib/api';

test('manual reporting refresh explicitly requests cache revalidation', () => {
  const controller = new AbortController();
  const normal = reportingRequestInit(controller.signal);
  const forced = reportingRequestInit(controller.signal, true);

  assert.equal(normal.signal, controller.signal);
  assert.equal(new Headers(normal.headers).get('Cache-Control'), null);
  assert.equal(forced.signal, controller.signal);
  assert.equal(new Headers(forced.headers).get('Cache-Control'), 'no-cache');
});

test('auto-refresh reporting requests are marked as background', () => {
  const controller = new AbortController();
  const user = reportingRequestInit(controller.signal);
  const background = reportingRequestInit(controller.signal, false, true);
  const forcedBackground = reportingRequestInit(controller.signal, true, true);

  assert.equal(new Headers(user.headers).get('X-DeltaLLM-Background'), null);
  assert.equal(new Headers(background.headers).get('X-DeltaLLM-Background'), '1');
  assert.equal(new Headers(forcedBackground.headers).get('X-DeltaLLM-Background'), '1');
  assert.equal(new Headers(forcedBackground.headers).get('Cache-Control'), 'no-cache');
});
