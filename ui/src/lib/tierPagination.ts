export function visibleTierPaginationPages(
  currentPage: number,
  totalPages: number,
): Array<number | null> {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const pages = new Set([1, totalPages, currentPage - 1, currentPage, currentPage + 1]);
  const ordered = [...pages]
    .filter((page) => page >= 1 && page <= totalPages)
    .sort((left, right) => left - right);
  const result: Array<number | null> = [];
  for (const page of ordered) {
    const previous = result[result.length - 1];
    if (typeof previous === 'number' && page - previous > 1) result.push(null);
    result.push(page);
  }
  return result;
}

export function clampTierPaginationOffset(
  total: number,
  pageSize: number,
  offset: number,
): number {
  const normalizedPageSize = Number.isFinite(pageSize) && pageSize > 0
    ? Math.floor(pageSize)
    : 10;
  const normalizedOffset = Number.isFinite(offset) && offset > 0
    ? Math.floor(offset / normalizedPageSize) * normalizedPageSize
    : 0;
  if (!Number.isFinite(total) || total <= 0) return 0;
  const lastPageOffset = Math.floor((Math.floor(total) - 1) / normalizedPageSize)
    * normalizedPageSize;
  return Math.min(normalizedOffset, lastPageOffset);
}
