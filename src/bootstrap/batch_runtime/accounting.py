"""Select the shared native batch owner once at the bootstrap edge."""

from __future__ import annotations

import logging

from prisma import Prisma
from starlette.datastructures import State

from src.batch.accounting_native import NativeBatchBilling
from src.batch.repositories.accounting_repository import BatchAccountingRepository
from src.batch.repository import BatchRepository
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.bootstrap.accounting_config import read_accounting_settings
from src.config import GeneralSettings
from src.services.tiers.tier_policy_service import TierPolicyService

logger = logging.getLogger(__name__)


def build_native_batch_billing(
    state: State,
    general: GeneralSettings,
    repository: BatchRepository,
) -> NativeBatchBilling | None:
    settings = getattr(state, "settings", None)
    if settings is None:
        if getattr(general, "accounting_protocol_enabled", False):
            raise RuntimeError("Native batch billing settings are unavailable")
        return None
    config = read_accounting_settings(general, settings)
    if not config.accounting_protocol_enabled:
        return None
    accounting = getattr(state, "accounting_protocol_service", None)
    tier_policy = getattr(state, "tier_policy_service", None)
    if (
        not isinstance(accounting, AccountingProtocolService)
        or not isinstance(repository.prisma, Prisma)
        or (tier_policy is not None and not isinstance(tier_policy, TierPolicyService))
    ):
        raise RuntimeError("Native batch billing dependencies are unavailable")
    return NativeBatchBilling(
        accounting,
        BatchAccountingRepository(
            repository.prisma,
            max_concurrent_writes=batch_checkpoint_write_concurrency(general, settings),
        ),
        tier_policy=tier_policy,
        max_provider_attempts=config.accounting_max_provider_attempts,
        checkpoint_write_seconds=general.embeddings_batch_accounting_write_timeout_seconds,
    )


def batch_checkpoint_write_concurrency(general: GeneralSettings, settings: object) -> int:
    pool_size = int(getattr(settings, "db_pool_size", None) or general.db_pool_size)
    if general.embeddings_batch_worker_concurrency > pool_size:
        logger.warning(
            "batch worker concurrency %s exceeds db_pool_size %s; "
            "items will queue for database writes, so add worker pods instead",
            general.embeddings_batch_worker_concurrency,
            pool_size,
        )
    configured = general.embeddings_batch_accounting_write_concurrency
    if configured > 0:
        return configured
    return max(1, pool_size // 2)
