export function routeGroupDetailPath(routeGroupId: string): string {
  return `/route-groups/by-id/${encodeURIComponent(routeGroupId)}`;
}

export function legacyRouteGroupKey(pathname: string): string | null {
  const prefix = '/route-groups/';
  if (!pathname.startsWith(prefix)) return null;
  try {
    // Read the original pathname once: router params can already have decoded
    // literal percent sequences, making "vendor%2Fmodel" look like "vendor/model".
    return decodeURIComponent(pathname.slice(prefix.length)) || null;
  } catch {
    return null;
  }
}
