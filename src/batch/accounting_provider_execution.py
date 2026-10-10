"""Use one typed batch provider seam for native admission and owned cleanup."""

from __future__ import annotations

from collections.abc import Sequence
import logging

from src.batch.accounting_checkpoint import BatchAccountingUnavailable
from src.batch.accounting_execution import BatchAccountingExecution
from src.batch.accounting_native import NativeBatchBilling
from src.batch.models import BatchJobRecord
from src.batch.worker_types import _PreparedChatItem, _PreparedEmbeddingItem
from src.models.requests import EmbeddingRequest
from src.router.router import Deployment

logger = logging.getLogger(__name__)
PreparedBatchItem = _PreparedChatItem | _PreparedEmbeddingItem


class AccountingProviderExecutionMixin:
    native_billing: NativeBatchBilling | None = None

    async def _fund_native_batch_attempt(
        self,
        job: BatchJobRecord,
        prepared_items: Sequence[PreparedBatchItem],
        deployment: Deployment,
    ) -> None:
        owner = self.native_billing
        if owner is None:
            if any(prepared.item.accounting_checkpoint is not None for prepared in prepared_items):
                raise BatchAccountingUnavailable()
            return
        executions = []
        for prepared in prepared_items:
            if prepared.native_accounting is None:
                prepared.native_accounting = owner.bind(
                    job,
                    prepared.item,
                    worker_id=self.config.worker_id,
                    auth=prepared.policy_auth,
                )
            executions.append(prepared.native_accounting)
        await owner.prepare_group(
            executions,
            payloads=[prepared.payload for prepared in prepared_items],
            auths=[prepared.policy_auth for prepared in prepared_items],
            deployment=deployment,
            selector_items=frozenset(
                prepared.item.item_id
                for prepared in prepared_items
                if isinstance(prepared, _PreparedChatItem) and prepared.selector is not None
            ),
        )

    async def _execute_batch_embedding_attempt(
        self,
        job: BatchJobRecord,
        prepared_items: Sequence[_PreparedEmbeddingItem],
        payload: EmbeddingRequest,
        deployment: Deployment,
    ) -> tuple[dict[str, object], str | None, str | None]:
        await self._fund_native_batch_attempt(job, prepared_items, deployment)
        data = await self._execute_embedding(prepared_items[0].request_shim, payload, deployment)
        return self._sanitize_embedding_response(data)

    async def _execute_batch_chat_attempt(
        self,
        job: BatchJobRecord,
        prepared: _PreparedChatItem,
        deployment: Deployment,
    ) -> tuple[dict[str, object], float]:
        await self._fund_native_batch_attempt(job, [prepared], deployment)
        return await self._execute_chat(
            prepared.request_shim,
            prepared.payload,
            deployment,
            record_usage=False,
        )

    async def _close_native_batch_items(self, prepared_items: Sequence[PreparedBatchItem]) -> None:
        owner = self.native_billing
        if owner is None:
            return
        executions = [
            prepared.native_accounting
            for prepared in prepared_items
            if prepared.native_accounting is not None
        ]
        try:
            await owner.close_group(executions)
        except (BatchAccountingUnavailable, TimeoutError):
            # The shared grant retains its debit. The durable claim can recover.
            logger.warning("Native batch cleanup remains pending under its durable claim")

    @staticmethod
    def _native_completion_saved(prepared_items: Sequence[PreparedBatchItem]) -> None:
        for prepared in prepared_items:
            if prepared.native_accounting is not None:
                prepared.native_accounting.completion_saved = True

    @staticmethod
    def _require_native_execution(prepared: PreparedBatchItem) -> BatchAccountingExecution:
        if prepared.native_accounting is None:
            raise BatchAccountingUnavailable()
        return prepared.native_accounting
