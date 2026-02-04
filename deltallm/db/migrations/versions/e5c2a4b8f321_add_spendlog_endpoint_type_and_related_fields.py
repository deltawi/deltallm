"""Add endpoint_type and related fields to spend_logs

Revision ID: e5c2a4b8f321
Revises: adcc7d8e7509
Create Date: 2026-02-01 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5c2a4b8f321'
down_revision: Union[str, None] = 'adcc7d8e7509'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add endpoint_type column
    op.add_column(
        'spend_logs',
        sa.Column(
            'endpoint_type',
            sa.String(length=50),
            nullable=False,
            server_default='chat'
        )
    )
    
    # Add audio/video/image specific columns
    op.add_column(
        'spend_logs',
        sa.Column(
            'audio_seconds',
            sa.Numeric(precision=10, scale=3),
            nullable=True
        )
    )
    
    op.add_column(
        'spend_logs',
        sa.Column(
            'audio_characters',
            sa.Integer(),
            nullable=True
        )
    )
    
    op.add_column(
        'spend_logs',
        sa.Column(
            'image_count',
            sa.Integer(),
            nullable=True
        )
    )
    
    op.add_column(
        'spend_logs',
        sa.Column(
            'image_size',
            sa.String(length=50),
            nullable=True
        )
    )
    
    op.add_column(
        'spend_logs',
        sa.Column(
            'rerank_searches',
            sa.Integer(),
            nullable=True
        )
    )
    
    # Create index on endpoint_type
    op.create_index(
        'idx_spend_logs_endpoint_type',
        'spend_logs',
        ['endpoint_type']
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop index
    op.drop_index('idx_spend_logs_endpoint_type', table_name='spend_logs')
    
    # Drop columns
    op.drop_column('spend_logs', 'rerank_searches')
    op.drop_column('spend_logs', 'image_size')
    op.drop_column('spend_logs', 'image_count')
    op.drop_column('spend_logs', 'audio_characters')
    op.drop_column('spend_logs', 'audio_seconds')
    op.drop_column('spend_logs', 'endpoint_type')
