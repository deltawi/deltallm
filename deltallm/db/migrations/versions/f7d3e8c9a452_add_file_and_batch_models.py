"""Add FileObject and BatchJob models

Revision ID: f7d3e8c9a452
Revises: e5c2a4b8f321
Create Date: 2026-02-01 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f7d3e8c9a452'
down_revision: Union[str, None] = 'e5c2a4b8f321'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create file_objects table
    op.create_table(
        'file_objects',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('bytes', sa.Integer(), nullable=False),
        sa.Column('purpose', sa.String(length=50), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('content_type', sa.String(length=100), nullable=True),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('api_key_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for file_objects
    op.create_index('idx_file_objects_org_id', 'file_objects', ['org_id'])
    op.create_index('idx_file_objects_api_key_id', 'file_objects', ['api_key_id'])
    op.create_index('idx_file_objects_purpose', 'file_objects', ['purpose'])
    op.create_index('idx_file_objects_created_at', 'file_objects', ['created_at'])
    
    # Create batch_jobs table
    op.create_table(
        'batch_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('endpoint', sa.String(length=255), nullable=False),
        sa.Column('completion_window', sa.String(length=50), nullable=False),
        sa.Column('input_file_id', sa.String(length=255), nullable=False),
        sa.Column('output_file_id', sa.String(length=255), nullable=True),
        sa.Column('error_file_id', sa.String(length=255), nullable=True),
        sa.Column('original_cost', sa.Numeric(precision=15, scale=6), nullable=True),
        sa.Column('discounted_cost', sa.Numeric(precision=15, scale=6), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('total_requests', sa.Integer(), nullable=True),
        sa.Column('completed_requests', sa.Integer(), nullable=True),
        sa.Column('failed_requests', sa.Integer(), nullable=True),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('api_key_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for batch_jobs
    op.create_index('idx_batch_jobs_status', 'batch_jobs', ['status'])
    op.create_index('idx_batch_jobs_org_id', 'batch_jobs', ['org_id'])
    op.create_index('idx_batch_jobs_api_key_id', 'batch_jobs', ['api_key_id'])
    op.create_index('idx_batch_jobs_input_file_id', 'batch_jobs', ['input_file_id'])
    op.create_index('idx_batch_jobs_created_at', 'batch_jobs', ['created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    # Drop batch_jobs indexes
    op.drop_index('idx_batch_jobs_created_at', table_name='batch_jobs')
    op.drop_index('idx_batch_jobs_input_file_id', table_name='batch_jobs')
    op.drop_index('idx_batch_jobs_api_key_id', table_name='batch_jobs')
    op.drop_index('idx_batch_jobs_org_id', table_name='batch_jobs')
    op.drop_index('idx_batch_jobs_status', table_name='batch_jobs')
    
    # Drop batch_jobs table
    op.drop_table('batch_jobs')
    
    # Drop file_objects indexes
    op.drop_index('idx_file_objects_created_at', table_name='file_objects')
    op.drop_index('idx_file_objects_purpose', table_name='file_objects')
    op.drop_index('idx_file_objects_api_key_id', table_name='file_objects')
    op.drop_index('idx_file_objects_org_id', table_name='file_objects')
    
    # Drop file_objects table
    op.drop_table('file_objects')
