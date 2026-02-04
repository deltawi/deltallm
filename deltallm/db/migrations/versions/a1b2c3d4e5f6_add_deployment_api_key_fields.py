"""Add deployment-level API key fields.

Revision ID: 20260202_add_deployment_api_key_fields
Revises: f7d3e8c9a452
Create Date: 2026-02-02 15:20:03.000000

This migration adds support for LiteLLM-style model management where
API keys can be stored at the ModelDeployment level instead of only
at the ProviderConfig level.

Changes:
- Add api_key_encrypted column to model_deployments
- Add api_base column to model_deployments  
- Add provider_type column to model_deployments
- Make provider_config_id nullable to support standalone deployments

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f7d3e8c9a452'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add deployment-level API key fields for standalone deployments."""
    
    # Add new columns to model_deployments table
    op.add_column(
        'model_deployments',
        sa.Column(
            'provider_type',
            sa.String(length=50),
            nullable=True,
            comment="Provider type for standalone deployments (openai, anthropic, etc.)"
        )
    )
    
    op.add_column(
        'model_deployments',
        sa.Column(
            'api_key_encrypted',
            sa.Text(),
            nullable=True,
            comment="Encrypted API key for standalone deployments (Fernet encryption)"
        )
    )
    
    op.add_column(
        'model_deployments',
        sa.Column(
            'api_base',
            sa.String(length=500),
            nullable=True,
            comment="Custom API base URL for standalone deployments"
        )
    )
    
    # Make provider_config_id nullable to support standalone deployments
    op.alter_column(
        'model_deployments',
        'provider_config_id',
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
        comment="Reference to provider configuration (NULL = standalone deployment)"
    )
    
    # Create index on provider_type for filtering
    op.create_index(
        'ix_model_deployments_provider_type',
        'model_deployments',
        ['provider_type'],
        unique=False
    )
    
    # Note: We keep the existing unique constraint uq_deployment_model_provider
    # but it will only apply to rows where provider_config_id is not NULL.
    # For standalone deployments (provider_config_id IS NULL), multiple deployments
    # with the same model_name can exist (each with different API keys).


def downgrade() -> None:
    """Remove deployment-level API key fields."""
    
    # Drop the index
    op.drop_index('ix_model_deployments_provider_type', table_name='model_deployments')
    
    # Drop the columns
    op.drop_column('model_deployments', 'api_base')
    op.drop_column('model_deployments', 'api_key_encrypted')
    op.drop_column('model_deployments', 'provider_type')
    
    # Restore provider_config_id to non-nullable
    # Note: This will fail if there are any standalone deployments
    # (rows with provider_config_id IS NULL)
    op.alter_column(
        'model_deployments',
        'provider_config_id',
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
        comment="Reference to provider configuration"
    )
