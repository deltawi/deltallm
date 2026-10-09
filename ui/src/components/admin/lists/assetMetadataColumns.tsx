import type { Column } from '../../DataTable';
import { CopyIdentifier, UpdatedAt, Visibility } from './AssetListCells';

interface ListMetadata {
  created_by_user_id?: string | null;
  updated_at?: string | null;
  visibility?: string | null;
  access?: { visibility: string } | null;
}
export function assetMetadataColumns<T extends ListMetadata>(): Column<T>[] {
  return [
    { key: 'created_by', header: 'Created by', sortKey: 'created_by', render: (row) => <CopyIdentifier value={row.created_by_user_id} /> },
    { key: 'updated_at', header: 'Updated at', sortKey: 'updated_at', defaultDirection: 'desc', render: (row) => <UpdatedAt value={row.updated_at} /> },
    { key: 'visibility', header: 'Visibility', sortKey: 'visibility', render: (row) => <Visibility value={row.visibility || row.access?.visibility} /> },
  ];
}
