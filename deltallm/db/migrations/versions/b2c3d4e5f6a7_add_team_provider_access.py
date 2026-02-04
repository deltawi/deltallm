"""Add team_provider_access table.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-02 16:00:00.000000

This migration adds the team_provider_access table which enables
fine-grained access control where org admins can grant specific
teams access to specific provider configurations.

Changes:
- Create team_provider_access table
- Add indexes on team_id and provider_config_id
- Add unique constraint to prevent duplicate grants

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create team_provider_access table."""

    op.create_table(
        'team_provider_access',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'team_id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Team ID"
        ),
        sa.Column(
            'provider_config_id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Provider configuration ID"
        ),
        sa.Column(
            'granted_by',
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="User who granted this access"
        ),
        sa.Column(
            'granted_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()'),
            comment="When access was granted"
        ),
        sa.ForeignKeyConstraint(
            ['team_id'],
            ['teams.id'],
            name='fk_team_provider_access_team_id',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['provider_config_id'],
            ['provider_configs.id'],
            name='fk_team_provider_access_provider_config_id',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['granted_by'],
            ['users.id'],
            name='fk_team_provider_access_granted_by',
            ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('team_id', 'provider_config_id', name='uq_team_provider_access')
    )

    # Create indexes for efficient lookups
    op.create_index(
        'ix_team_provider_access_team_id',
        'team_provider_access',
        ['team_id'],
        unique=False
    )

    op.create_index(
        'ix_team_provider_access_provider_config_id',
        'team_provider_access',
        ['provider_config_id'],
        unique=False
    )


def downgrade() -> None:
    """Drop team_provider_access table."""

    # Drop indexes
    op.drop_index('ix_team_provider_access_provider_config_id', table_name='team_provider_access')
    op.drop_index('ix_team_provider_access_team_id', table_name='team_provider_access')

    # Drop table
    op.drop_table('team_provider_access')
