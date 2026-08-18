export const RETURN_TO_PARAM = 'returnTo';

const PUBLIC_AUTH_PATHS = new Set([
  '/login',
  '/forgot-password',
  '/reset-password',
  '/accept-invite',
]);

export function safeReturnTo(value: string | null | undefined, fallback = '/'): string {
  const candidate = String(value || '').trim();
  if (!candidate || candidate.length > 2048) return fallback;
  if (!candidate.startsWith('/') || candidate.startsWith('//') || candidate.includes('\\')) return fallback;
  const path = candidate.split(/[?#]/, 1)[0];
  const normalizedPath = path.replace(/\/+$/, '') || '/';
  if (PUBLIC_AUTH_PATHS.has(normalizedPath)) return fallback;
  if ([...candidate].some((character) => character.charCodeAt(0) < 32)) return fallback;
  return candidate;
}

export function loginPathFor(returnTo: string): string {
  const params = new URLSearchParams({ [RETURN_TO_PARAM]: safeReturnTo(returnTo) });
  return `/login?${params.toString()}`;
}

export function returnToFromSearch(search: string, fallback = '/'): string {
  const params = new URLSearchParams(search);
  return safeReturnTo(params.get(RETURN_TO_PARAM), fallback);
}
