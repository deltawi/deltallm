"""Add model_type column to model_deployments.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-02-02 17:00:00.000000

This migration adds model type classification to model deployments,
enabling proper routing, validation, and capability matching for
different model types (Chat, Embedding, TTS, STT, Image Generation, etc.).

Changes:
- Add model_type column to model_deployments
- Add index on model_type for filtering
- Auto-classify existing deployments based on name patterns

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add model_type column with pattern-based auto-classification."""

    # Add model_type column with default 'chat'
    op.add_column(
        'model_deployments',
        sa.Column(
            'model_type',
            sa.String(length=50),
            nullable=False,
            server_default='chat',
            comment="Model type (chat, embedding, image_generation, audio_transcription, audio_speech, rerank, moderation)"
        )
    )

    # Create index for efficient filtering by type
    op.create_index(
        'ix_model_deployments_model_type',
        'model_deployments',
        ['model_type'],
        unique=False
    )

    # Auto-classify existing deployments based on name patterns
    # This uses SQL CASE statements to match common patterns
    op.execute("""
        UPDATE model_deployments SET model_type = CASE
            WHEN LOWER(model_name) LIKE '%embed%' OR LOWER(provider_model) LIKE '%embed%' THEN 'embedding'
            WHEN LOWER(model_name) LIKE '%whisper%' OR LOWER(provider_model) LIKE '%whisper%' THEN 'audio_transcription'
            WHEN LOWER(model_name) LIKE '%tts%' OR LOWER(provider_model) LIKE '%tts%' THEN 'audio_speech'
            WHEN LOWER(model_name) LIKE '%dall%' OR LOWER(provider_model) LIKE '%dall%' THEN 'image_generation'
            WHEN LOWER(model_name) LIKE '%imagen%' OR LOWER(provider_model) LIKE '%imagen%' THEN 'image_generation'
            WHEN LOWER(model_name) LIKE '%stable-diffusion%' OR LOWER(provider_model) LIKE '%stable-diffusion%' THEN 'image_generation'
            WHEN LOWER(model_name) LIKE '%rerank%' OR LOWER(provider_model) LIKE '%rerank%' THEN 'rerank'
            WHEN LOWER(model_name) LIKE '%moderation%' OR LOWER(provider_model) LIKE '%moderation%' THEN 'moderation'
            ELSE 'chat'
        END
    """)


def downgrade() -> None:
    """Remove model_type column."""

    # Drop the index
    op.drop_index('ix_model_deployments_model_type', table_name='model_deployments')

    # Drop the column
    op.drop_column('model_deployments', 'model_type')
