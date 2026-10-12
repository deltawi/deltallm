import type { Column } from '../../DataTable';
import { CreatedBy, UpdatedAt, Visibility } from './AssetListCells';
import type { AssetCreatorFields } from '../../../lib/api/managedAssets';

interface ListMetadata extends AssetCreatorFields {
  updated_at?: string | null;
  visibility?: string | null;
  access?: { visibility: string } | null;
}
export function assetMetadataColumns<T extends ListMetadata>(): Column<T>[] {
  return [
    { key: 'created_by', header: 'Created by', sortKey: 'created_by', render: (row) => <CreatedBy row={row} /> },
    { key: 'updated_at', header: 'Updated at', sortKey: 'updated_at', defaultDirection: 'desc', render: (row) => <UpdatedAt value={row.updated_at} /> },
    { key: 'visibility', header: 'Visibility', sortKey: 'visibility', render: (row) => <Visibility value={row.visibility || row.access?.visibility} /> },
  ];
}
