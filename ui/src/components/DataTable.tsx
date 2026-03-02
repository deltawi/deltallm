import type { ReactNode } from 'react';
import type { Pagination } from '../lib/api';
import { ChevronLeft, ChevronRight } from 'lucide-react';

interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  className?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  loading?: boolean;
  emptyMessage?: string;
  onRowClick?: (row: T) => void;
  pagination?: Pagination;
  onPageChange?: (offset: number) => void;
}

export default function DataTable<T extends Record<string, any>>({
  columns,
  data,
  loading,
  emptyMessage = 'No data found',
  onRowClick,
  pagination,
  onPageChange,
}: DataTableProps<T>) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  const rows = Array.isArray(data) ? data : [];

  const currentPage = pagination ? Math.floor(pagination.offset / pagination.limit) + 1 : 1;
  const totalPages = pagination ? Math.ceil(pagination.total / pagination.limit) : 1;

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-200">
              {columns.map((col) => (
                <th key={col.key} className={`text-left text-xs font-medium text-gray-500 uppercase tracking-wider py-3 px-4 ${col.className || ''}`}>
                  {col.header}
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
                  key={i}
                  onClick={() => onRowClick?.(row)}
                  className={`border-b border-gray-100 ${onRowClick ? 'cursor-pointer hover:bg-gray-50' : ''}`}
                >
                  {columns.map((col) => (
                    <td key={col.key} className={`py-3 px-4 text-sm text-gray-700 ${col.className || ''}`}>
                      {col.render ? col.render(row) : String(row[col.key] ?? '')}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {pagination && pagination.total > 0 && onPageChange && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100">
          <span className="text-xs text-gray-500">
            Showing {pagination.offset + 1}–{Math.min(pagination.offset + pagination.limit, pagination.total)} of {pagination.total}
          </span>
          {totalPages > 1 && (
            <div className="flex items-center gap-1">
              <button
                onClick={() => onPageChange(Math.max(0, pagination.offset - pagination.limit))}
                disabled={pagination.offset === 0}
                className="p-1.5 rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-xs text-gray-600 px-2">
                Page {currentPage} of {totalPages}
              </span>
              <button
                onClick={() => onPageChange(pagination.offset + pagination.limit)}
                disabled={!pagination.has_more}
                className="p-1.5 rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
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
