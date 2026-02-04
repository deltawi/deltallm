"""Increase spend column precision in spend_logs table.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-02-04 12:00:00.000000

This migration increases the precision of the spend column from 6 to 12 decimal
places to match the precision used in model_pricing table.

This is necessary because per-token costs can be very small (e.g., $0.000000059
for Groq's llama-3.1-8b-instant), and 6 decimal places caused truncation that
resulted in $0.00 being stored for small token counts.

Changes:
- Alter spend column from NUMERIC(15, 6) to NUMERIC(20, 12)

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Increase spend column precision to 12 decimal places."""
    op.alter_column(
        'spend_logs',
        'spend',
        type_=sa.Numeric(20, 12),
        comment='Cost of the request in USD (12 decimal places for per-token precision)',
    )


def downgrade() -> None:
    """Revert spend column precision to 6 decimal places.

    Note: This may cause precision loss for any data stored with more
    than 6 decimal places.
    """
    op.alter_column(
        'spend_logs',
        'spend',
        type_=sa.Numeric(15, 6),
        comment='Cost of the request in USD',
    )
