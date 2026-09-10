"""Webhook boundary over the shared outbound destination policy."""

from src.batch.webhooks.models import BatchWebhookRequest
from src.outbound.network_policy import (
    OutboundNetworkPolicy,
    OutboundPolicyError as BatchWebhookNetworkPolicyError,
    OutboundResolutionError as BatchWebhookResolutionError,
    ResolvedOutboundTarget as ResolvedBatchWebhookTarget,
)

__all__ = [
    "BatchWebhookNetworkPolicy",
    "BatchWebhookNetworkPolicyError",
    "BatchWebhookResolutionError",
    "ResolvedBatchWebhookTarget",
]


class BatchWebhookNetworkPolicy(OutboundNetworkPolicy):
    async def resolve(
        self, config: BatchWebhookRequest | str, *, attempt_count: int
    ) -> ResolvedBatchWebhookTarget:
        return await super().resolve(
            config.url if isinstance(config, BatchWebhookRequest) else config,
            attempt_count=attempt_count,
        )
