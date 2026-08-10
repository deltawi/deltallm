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
