import assert from 'node:assert/strict';
import test from 'node:test';

import { branding } from '../src/lib/api';

const responsePayload = {
  instance_name: 'DeltaLLM',
  logo_mark_url: null,
  logo_full_url: null,
  favicon_url: null,
  primary_color: '#2563EB',
  secondary_color: '#7C3AED',
  menu_hover_color: '#F9FAFB',
};

test('branding asset uploads let the browser set the multipart boundary', async () => {
  const originalFetch = globalThis.fetch;
  let capturedInput: RequestInfo | URL | undefined;
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    capturedInput = input;
    capturedInit = init;
    return new Response(JSON.stringify(responsePayload), {
      headers: { 'content-type': 'application/json' },
    });
  }) as typeof fetch;

  try {
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'mark.png', { type: 'image/png' });
    await branding.uploadAsset('logo_mark', file);

    assert.equal(capturedInput, '/ui/api/branding/assets/logo_mark');
    assert.equal(capturedInit?.method, 'PUT');
    assert.ok(capturedInit?.body instanceof FormData);
    assert.equal(new Headers(capturedInit?.headers).has('content-type'), false);
    assert.equal((capturedInit?.body as FormData).get('file'), file);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
