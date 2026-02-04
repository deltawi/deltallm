"""Alembic environment configuration for async PostgreSQL.

This module configures Alembic for use with SQLAlchemy's async ORM.
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import Base and models for autogenerate support
from deltallm.db.base import Base
from deltallm.db.models import (  # noqa: F401
    APIKey,
    APIKeyPermission,
    AuditLog,
    BatchJob,
    FileObject,
    ModelDeployment,
    ModelPricing,
    OrgMember,
    Organization,
    Permission,
    ProviderConfig,
    Role,
    RolePermission,
    SpendLog,
    Team,
    TeamMember,
    TeamProviderAccess,
    User,
)

# this is the Alembic Config object
config = context.config

# Set up logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata

# Get database URL from environment or use default
def get_database_url() -> str:
    """Get database URL for migrations (uses sync driver)."""
    async_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://deltallm:deltallm@localhost:5432/deltallm"
    )
    # Convert asyncpg URL to psycopg2 for migrations
    return async_url.replace("+asyncpg", "+psycopg2")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.
    
    This configures the context with just a URL and not an Engine.
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode."""
    # Get configuration with database URL
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url().replace("+psycopg2", "+asyncpg")

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
