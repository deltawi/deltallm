import type { ReactNode } from 'react';
import type { Pagination } from '../lib/api';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight } from 'lucide-react';

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  className?: string;
  sortKey?: string;
  defaultDirection?: 'asc' | 'desc';
}

export interface TableSort { key: string; direction: 'asc' | 'desc' }

interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  loading?: boolean;
  emptyMessage?: string;
  onRowClick?: (row: T) => void;
  pagination?: Pagination;
  onPageChange?: (offset: number) => void;
  onPreviousPage?: () => void;
  onNextPage?: () => void;
  sort?: TableSort;
  onSortChange?: (sort: TableSort) => void;
  responsive?: boolean;
  rowKey?: (row: T) => string;
  caption?: string;
}

export default function DataTable<T extends object>({
  columns,
  data,
  loading,
  emptyMessage = 'No data found',
  onRowClick,
  pagination,
  onPageChange,
  onPreviousPage,
  onNextPage,
  sort,
  onSortChange,
  responsive = false,
  rowKey,
  caption,
}: DataTableProps<T>) {
  if (loading) {
    return (
      <div role="status" className="relative flex items-center justify-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-brand-primary" />
        <span className="sr-only">Loading list…</span>
      </div>
    );
  }

  const rows = Array.isArray(data) ? data : [];

  const { offset, limit, total } = pagination ?? { offset: 0, limit: 1, total: 0 };
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);
  const renderCell = (column: Column<T>, row: T) => column.render
    ? column.render(row) : String((row as Record<string, unknown>)[column.key] ?? '');

  return (
    <div>
      <div className={`relative overflow-x-auto ${responsive ? 'hidden md:block' : ''}`}>
        <table className="w-full">
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <thead>
            <tr className="border-b border-gray-200">
              {columns.map((col) => (
                <th key={col.key} scope="col" aria-sort={sort && col.sortKey && sort.key === col.sortKey ? sort.direction === 'asc' ? 'ascending' : 'descending' : undefined} className={`text-left text-xs font-medium text-gray-500 py-3 px-4 ${responsive ? 'bg-gray-50 whitespace-nowrap' : 'uppercase tracking-wider'} ${col.className || ''}`}>
                  {col.sortKey && onSortChange ? (
                    <button type="button" aria-label={`Sort by ${col.header}`} className={`inline-flex items-center gap-1.5 rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary ${sort?.key === col.sortKey ? 'text-brand-primary-ink' : ''}`} onClick={() => onSortChange({ key: col.sortKey!, direction: sort && sort.key === col.sortKey ? sort.direction === 'asc' ? 'desc' : 'asc' : col.defaultDirection || 'asc' })}>
                      {col.header}
                      {sort?.key !== col.sortKey ? <ArrowUpDown className="h-3 w-3 text-gray-400" /> : sort?.direction === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />}
                    </button>
                  ) : col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="text-center py-12 text-gray-400 text-sm">
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              rows.map((row, i) => (
                <tr
                  key={rowKey?.(row) ?? i}
                  onClick={() => onRowClick?.(row)}
                  onKeyDown={(event) => {
                    if (!onRowClick) return;
                    if (event.target !== event.currentTarget) return;
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      onRowClick(row);
                    }
                  }}
                  tabIndex={onRowClick && !responsive ? 0 : undefined}
                  aria-label={onRowClick ? 'Open row details' : undefined}
                  className={`border-b border-gray-100 ${onRowClick ? 'cursor-pointer hover:bg-gray-50 focus:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-inset' : ''}`}
                >
                  {columns.map((col) => (
                    <td key={col.key} className={`py-3 px-4 text-sm text-gray-700 ${col.className || ''}`}>
                      {renderCell(col, row)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {responsive ? (
        <div className="divide-y divide-gray-100 md:hidden" aria-label={caption}>
          {rows.length ? rows.map((row, index) => (
            <article key={rowKey?.(row) ?? index} className="p-4">
              <div className="mb-4">{renderCell(columns[0], row)}</div>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                {columns.slice(1).map((column) => (
                  <div key={column.key} className={`min-w-0 ${['created_by', 'visibility'].includes(column.key) ? 'col-span-2' : ''}`}>
                    <dt className="mb-1 text-xs text-gray-500">{column.header}</dt>
                    <dd className="text-gray-700">{renderCell(column, row)}</dd>
                  </div>
                ))}
              </dl>
            </article>
          )) : <p className="px-4 py-12 text-center text-sm text-gray-400">{emptyMessage}</p>}
        </div>
      ) : null}
      {pagination && (total > 0 || offset > 0) && (onPageChange || (onPreviousPage && onNextPage)) && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100">
          <span className="text-xs text-gray-500">
            {`Showing ${rows.length ? `${offset + 1}–${Math.min(offset + rows.length, total)}` : '0'} of ${total}`}
          </span>
          {(totalPages > 1 || offset > 0) && (
            <div className="flex items-center gap-1">
              <button
                type="button"
                aria-label="Previous page"
                onClick={onPreviousPage ?? (() => onPageChange?.(Math.max(0, offset - limit)))}
                disabled={offset === 0}
                className="h-10 w-10 flex items-center justify-center rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              {currentPage <= totalPages && <span className="text-xs text-gray-600 px-2">
                Page {currentPage} of {totalPages}
              </span>}
              <button
                type="button"
                aria-label="Next page"
                onClick={onNextPage ?? (() => onPageChange?.(offset + limit))}
                disabled={!pagination.has_more}
                className="h-10 w-10 flex items-center justify-center rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
