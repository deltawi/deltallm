export interface UIMount {
  mount_path: string;
  external_console: boolean;
}

export function parseUIMount(value: unknown): Readonly<UIMount> {
  if (!value || typeof value !== 'object') throw new Error('Invalid UI mount configuration');
  const candidate = value as Record<string, unknown>;
  const mount = candidate.mount_path;
  if (typeof mount !== 'string' || (mount !== '' && !/^\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/.test(mount))) {
    throw new Error('Invalid UI mount path');
  }
  if (typeof candidate.external_console !== 'boolean') throw new Error('Invalid UI session source');
  if (candidate.external_console && !mount) throw new Error('Console UI requires a separate mount');
  if (Object.keys(candidate).some((key) => !['mount_path', 'external_console'].includes(key))) {
    throw new Error('Unknown UI mount setting');
  }
  return Object.freeze({ mount_path: mount, external_console: candidate.external_console });
}

export function uiMount(): Readonly<UIMount> {
  const config = typeof document === 'undefined' ? null : document.getElementById('deltallm-ui-config');
  return config ? parseUIMount(JSON.parse(config.textContent || '')) : Object.freeze({ mount_path: '', external_console: false });
}

export function mountedPath(path: string, mount = uiMount()): string {
  if (!path.startsWith('/') || path.startsWith('//') || (path.includes('\\') || [...path].some((character) => character.charCodeAt(0) <= 32))) {
    throw new Error('A same-origin path is required');
  }
  return `${mount.mount_path}${path}`;
}

export function consoleSignInPath(returnTo: string): string {
  const mount = uiMount();
  return mountedPath(`/console/login?${new URLSearchParams({ returnTo: mountedPath(returnTo, mount) })}`, mount);
}

export function mountedAssetPath(path: string): string {
  return path.startsWith('/') && !path.startsWith('//') ? mountedPath(path) : path;
}
