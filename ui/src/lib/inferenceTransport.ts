import { mountedPath, uiMount } from './uiMount';

export function inferenceFetch(path: string, apiKey: string, options: RequestInit): Promise<Response> {
  const headers = new Headers(options.headers);
  if (uiMount().external_console) {
    headers.delete('Authorization');
    headers.delete('X-Master-Key');
  } else {
    headers.set('Authorization', `Bearer ${apiKey}`);
  }
  return fetch(mountedPath(path), { ...options, credentials: 'same-origin', headers });
}
