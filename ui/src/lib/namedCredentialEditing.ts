export function matchingNamedCredentialDetail<T extends { credential_id: string }>(
  selected: { credential_id: string } | null,
  detail: T | null,
): T | null {
  if (!selected || detail?.credential_id !== selected.credential_id) return null;
  return detail;
}
