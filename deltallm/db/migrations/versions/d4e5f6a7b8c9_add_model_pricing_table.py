"""Add model_pricing table for persistent custom pricing.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-02-03 16:00:00.000000

This migration adds the model_pricing table for storing custom pricing
configurations that persist across application restarts.

Changes:
- Add model_pricing table with all pricing fields
- Add indexes for model_name, org_id, team_id
- Add unique constraint for model+org+team combination

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create model_pricing table."""
    op.create_table(
        'model_pricing',
        # Primary key and timestamps
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),

        # Scope columns
        sa.Column('model_name', sa.String(255), nullable=False, index=True, comment='Model name'),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True, index=True, comment='Organization ID (NULL = global)'),
        sa.Column('team_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('teams.id', ondelete='CASCADE'), nullable=True, index=True, comment='Team ID (NULL = org-level or global)'),

        # Pricing mode
        sa.Column('mode', sa.String(50), nullable=False, server_default='chat', comment='Pricing mode'),

        # Token-based pricing
        sa.Column('input_cost_per_token', sa.Numeric(20, 12), nullable=False, server_default='0', comment='Cost per input token'),
        sa.Column('output_cost_per_token', sa.Numeric(20, 12), nullable=False, server_default='0', comment='Cost per output token'),

        # Prompt caching
        sa.Column('cache_creation_input_token_cost', sa.Numeric(20, 12), nullable=True, comment='Cost to create cached tokens'),
        sa.Column('cache_read_input_token_cost', sa.Numeric(20, 12), nullable=True, comment='Cost to read cached tokens'),

        # Image generation
        sa.Column('image_cost_per_image', sa.Numeric(20, 12), nullable=True, comment='Cost per image'),
        sa.Column('image_sizes', postgresql.JSONB, nullable=False, server_default='{}', comment='Image size pricing map'),
        sa.Column('quality_pricing', postgresql.JSONB, nullable=False, server_default='{}', comment='Quality multipliers'),

        # Audio pricing
        sa.Column('audio_cost_per_character', sa.Numeric(20, 12), nullable=True, comment='TTS cost per character'),
        sa.Column('audio_cost_per_minute', sa.Numeric(20, 12), nullable=True, comment='STT cost per minute'),

        # Rerank pricing
        sa.Column('rerank_cost_per_search', sa.Numeric(20, 12), nullable=True, comment='Cost per rerank search'),

        # Batch settings
        sa.Column('batch_discount_percent', sa.Numeric(5, 2), nullable=False, server_default='50.0', comment='Batch discount percentage'),
        sa.Column('base_model', sa.String(255), nullable=True, comment='Base model for pricing inheritance'),

        # Limits
        sa.Column('max_tokens', sa.Integer, nullable=True, comment='Maximum context window'),
        sa.Column('max_input_tokens', sa.Integer, nullable=True, comment='Maximum input tokens'),
        sa.Column('max_output_tokens', sa.Integer, nullable=True, comment='Maximum output tokens'),

        # Unique constraint: one pricing per model+org+team combination
        sa.UniqueConstraint('model_name', 'org_id', 'team_id', name='uq_model_pricing_scope'),
    )


def downgrade() -> None:
    """Drop model_pricing table."""
    op.drop_table('model_pricing')
