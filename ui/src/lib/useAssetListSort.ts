import { useState } from 'react';
import type { TableSort } from '../components/DataTable';

export function useAssetListSort<Key extends string>(keys: readonly Key[], resetPage: () => void) {
  const [sortBy, setSortBy] = useState<Key>(keys.find((key) => key === 'updated_at') || keys[0]);
  const [sortDirection, setDirection] = useState<'asc' | 'desc'>('desc');
  const onSortChange = (sort: TableSort) => {
    const key = keys.find((candidate) => candidate === sort.key);
    if (!key) return;
    setSortBy(key); setDirection(sort.direction); resetPage();
  };
  return { sortBy, sortDirection, onSortChange, sort: { key: sortBy, direction: sortDirection } };
}
