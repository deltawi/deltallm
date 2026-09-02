export type OrganizationCount = number | null;

export function normalizeOrganizationCount(value: unknown): OrganizationCount {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

export function formatOrganizationCount(value: OrganizationCount): string {
  return value === null ? '—' : String(value);
}
