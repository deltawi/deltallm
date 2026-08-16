import { ChevronLeft, ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';
import type { Pagination } from '../../lib/api';
import { visibleTierPaginationPages } from '../../lib/tierPagination';

type TierPaginationProps = {
  pagination: Pagination;
  pageSize: number;
  onPageChange: (offset: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  disabled?: boolean;
  itemLabel?: string;
};

const PAGE_SIZES = [10, 25, 50];

export default function TierPagination({
  pagination,
  pageSize,
  onPageChange,
  onPageSizeChange,
  disabled = false,
  itemLabel = 'items',
}: TierPaginationProps) {
  const totalPages = Math.max(1, Math.ceil(pagination.total / pageSize));
  const currentPage = Math.min(totalPages, Math.floor(pagination.offset / pageSize) + 1);
  const pages = visibleTierPaginationPages(currentPage, totalPages);
  const first = pagination.total === 0 ? 0 : pagination.offset + 1;
  const last = Math.min(pagination.offset + pagination.limit, pagination.total);

  return (
    <nav
      aria-label={`${itemLabel} pagination`}
      className="flex flex-col gap-3 border-t border-gray-100 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex flex-wrap items-center gap-3 text-xs text-gray-500">
        <span aria-live="polite">Showing {first}–{last} of {pagination.total} {itemLabel}</span>
        {onPageSizeChange ? (
          <label className="inline-flex items-center gap-2">
            <span>Rows</span>
            <select
              aria-label={`Rows per page for ${itemLabel}`}
              value={pageSize}
              onChange={(event) => onPageSizeChange(Number(event.target.value))}
              disabled={disabled}
              className="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:opacity-50"
            >
              {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
            </select>
          </label>
        ) : null}
      </div>

      {totalPages > 1 ? (
        <div className="flex items-center gap-1">
          <PageButton
            label="Previous page"
            disabled={disabled || currentPage === 1}
            onClick={() => onPageChange(Math.max(0, (currentPage - 2) * pageSize))}
          >
            <ChevronLeft aria-hidden="true" className="h-4 w-4" />
          </PageButton>
          {pages.map((page, index) => page === null ? (
            <span key={`ellipsis-${index}`} className="px-1 text-xs text-gray-400" aria-hidden="true">…</span>
          ) : (
            <PageButton
              key={page}
              label={`Page ${page}`}
              current={page === currentPage}
              disabled={disabled}
              onClick={() => onPageChange((page - 1) * pageSize)}
            >
              {page}
            </PageButton>
          ))}
          <PageButton
            label="Next page"
            disabled={disabled || currentPage === totalPages}
            onClick={() => onPageChange(currentPage * pageSize)}
          >
            <ChevronRight aria-hidden="true" className="h-4 w-4" />
          </PageButton>
        </div>
      ) : null}
    </nav>
  );
}

function PageButton({
  label,
  current = false,
  disabled,
  onClick,
  children,
}: {
  label: string;
  current?: boolean;
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-current={current ? 'page' : undefined}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex h-8 min-w-8 items-center justify-center rounded-md border px-2 text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-40 ${
        current
          ? 'border-brand-primary bg-brand-primary text-brand-on-primary'
          : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
      }`}
    >
      {children}
    </button>
  );
}
