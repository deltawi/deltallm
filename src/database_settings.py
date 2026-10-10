"""Typed startup budgets shared by file and environment configuration."""

from pydantic import BaseModel, Field, model_validator


class DatabaseAllocationSettings(BaseModel):
    db_foreground_pool_size: int = Field(default=8, ge=1, le=100)
    telemetry_worker_db_pool_size: int = Field(default=5, ge=1, le=100)
    db_acquisition_timeout_seconds: float = Field(default=0.2, ge=0.001, le=30)
    db_statement_timeout_seconds: float = Field(default=1.0, ge=0.001, le=30)
    db_lock_timeout_seconds: float = Field(default=0.2, ge=0.001, le=30)
    db_transaction_timeout_seconds: float = Field(default=2.0, ge=0.001, le=60)
    db_background_statement_timeout_seconds: float = Field(default=5.0, ge=0.001, le=60)
    db_background_lock_timeout_seconds: float = Field(default=1.0, ge=0.001, le=60)
    db_background_transaction_timeout_seconds: float = Field(default=10.0, ge=0.001, le=120)

    @model_validator(mode="after")
    def validate_database_deadlines(self):
        for prefix in ("db_", "db_background_"):
            lock = getattr(self, prefix + "lock_timeout_seconds")
            statement = getattr(self, prefix + "statement_timeout_seconds")
            transaction = getattr(self, prefix + "transaction_timeout_seconds")
            if not lock <= statement <= transaction:
                raise ValueError(f"{prefix}deadlines require lock <= statement <= transaction")
        return self


DATABASE_ALLOCATION_FIELDS = frozenset(DatabaseAllocationSettings.model_fields)
