export interface Pagination {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  after_line_number?: number | null;
  next_after_line_number?: number | null;
}

export interface Paginated<T> {
  data: T[];
  pagination: Pagination;
}
