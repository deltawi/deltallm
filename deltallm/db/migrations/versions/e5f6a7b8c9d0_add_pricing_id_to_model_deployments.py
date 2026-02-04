"""Add pricing_id FK to model_deployments table.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-02-04 10:00:00.000000

This migration adds a pricing_id foreign key column to the model_deployments
table to link deployments directly to their pricing configuration.

Changes:
- Add pricing_id column to model_deployments table
- Add FK constraint to model_pricing table
- Add index on pricing_id for query performance

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add pricing_id column to model_deployments."""
    op.add_column(
        'model_deployments',
        sa.Column(
            'pricing_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('model_pricing.id', ondelete='SET NULL'),
            nullable=True,
            comment='Reference to pricing configuration',
        ),
    )
    op.create_index(
        'ix_model_deployments_pricing_id',
        'model_deployments',
        ['pricing_id'],
    )


def downgrade() -> None:
    """Remove pricing_id column from model_deployments."""
    op.drop_index('ix_model_deployments_pricing_id', table_name='model_deployments')
    op.drop_column('model_deployments', 'pricing_id')
