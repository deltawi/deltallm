from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from hashlib import sha256
import json

from pydantic import ValidationError

from src.batch.models import BatchItemRecord, BatchJobRecord, BatchJobStatus
from src.batch.selector_checkpoint import (
    CHECKPOINT_MAX_BYTES,
    BatchSelectorCheckpoint,
    BatchSelectorCheckpoints,
    BatchSelectorClaim,
    BatchSelectorUnavailable,
)
from src.batch.selector_identity import batch_selector_operation_id
from src.cache.execution_eligibility import ResponseCacheEligibility, ResponseCacheOutcome
from src.models.requests import ChatCompletionRequest
from src.router.execution import RequestDeadline
from src.router.runtime_generation import RoutingRuntimeGeneration
from src.router.router import Deployment
from src.router.selection.contracts import SelectorDecision, SelectorPolicyIdentity
from src.router.selection.operation import (
    SelectorOperation,
    SelectorPrincipal,
    bind_selector_preparation,
)
from src.router.selection.request_context import set_request_selector_state
from src.router.selection.request_state import RequestSelectorState
from src.router.selection.runtime import SelectorExecutionFactory


def _read_checkpoint(raw: object) -> BatchSelectorCheckpoint:
    try:
        serialized = raw if isinstance(raw, str) else json.dumps(raw)
        if len(serialized.encode()) > CHECKPOINT_MAX_BYTES:
            raise BatchSelectorUnavailable()
        return BatchSelectorCheckpoint.model_validate_json(serialized)
    except (TypeError, ValueError, ValidationError):
        raise BatchSelectorUnavailable() from None


async def _run(work: Awaitable[SelectorDecision]) -> SelectorDecision:
    # The Batch item execution owner wraps the whole operation in its lease-loss
    # cancellation scope. No detached task, HTTP shim, or additional retry owner.
    return await work


def _batch_deadline(
    runtime: RoutingRuntimeGeneration, group: str, job: BatchJobRecord
) -> RequestDeadline:
    if job.status is not BatchJobStatus.IN_PROGRESS or job.cancel_requested_at is not None:
        raise BatchSelectorUnavailable()
    policy = runtime.router.config.route_group_policies.get(group)
    deadline = runtime.failover_manager.create_request_deadline(
        policy.timeout_seconds if policy else None
    )
    if job.expires_at is not None:
        remaining = (job.expires_at.astimezone(UTC) - datetime.now(UTC)).total_seconds()
        deadline = RequestDeadline.after(min(deadline.remaining(), max(0.0, remaining)))
    return deadline


class BatchSelectorExecution:
    """Adapt a claimed item to the canonical selector; persist only its decision."""

    def __init__(
        self,
        *,
        runtime: RoutingRuntimeGeneration,
        factory: SelectorExecutionFactory,
        checkpoints: BatchSelectorCheckpoints,
        job: BatchJobRecord,
        item: BatchItemRecord,
        worker_id: str,
        payload: ChatCompletionRequest,
        token_estimate: int,
        authorize: Callable[[str], None],
        context: dict[str, object],
    ) -> None:
        if (
            not job.created_by_api_key
            or not job.created_by_owner_snapshot_complete
            or item.batch_id != job.batch_id
        ):
            raise BatchSelectorUnavailable()
        principal = SelectorPrincipal(
            api_key=job.created_by_api_key,
            user_id=job.created_by_user_id,
            team_id=job.created_by_team_id,
            organization_id=job.created_by_organization_id,
            owner_account_id=job.created_by_owner_account_id,
        )
        self.operation_id = batch_selector_operation_id(job.batch_id, item.item_id)
        self._claim = BatchSelectorClaim(
            job.batch_id, item.item_id, principal.api_key, worker_id, item.claim_epoch
        )
        self._item, self._checkpoints = item, checkpoints
        # Content is hashed, never stored in the checkpoint or emitted to logs.
        self._fingerprint = sha256(
            json.dumps(
                {
                    "payload": payload.model_dump(mode="json"),
                    "principal": principal.model_dump(mode="json"),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        group = runtime.router.resolve_model_group(payload.model)
        deadline = _batch_deadline(runtime, group, job)
        self.deadline = deadline
        state = RequestSelectorState(deadline)
        self._pending: BatchSelectorCheckpoint | None = None
        self.decision: SelectorDecision | None = None
        if item.selector_checkpoint is not None:
            self._restore(runtime, item.selector_checkpoint, state, authorize)
        set_request_selector_state(context, state)
        self.operation = SelectorOperation(
            state=state,
            selectors=runtime.selectors,
            factory=factory,
            principal=principal,
            operation_id=self.operation_id,
            model=payload.model,
            payload=payload,
            token_estimate=token_estimate,
            cache=ResponseCacheEligibility(ResponseCacheOutcome.BYPASS),
            capacity_owner=runtime.router.state,
            authorize=authorize,
            observe=self._observe,
            run_connected=_run,
            context=context,
            planner=runtime.router,
            checkpoint=self,
        )
        self.operation.answer_observation.decision = self.decision
        bind_selector_preparation(context, self.operation)

    def _restore(
        self,
        runtime: RoutingRuntimeGeneration,
        raw: object,
        state: RequestSelectorState,
        authorize: Callable[[str], None],
    ) -> None:
        checkpoint = _read_checkpoint(raw)
        qualified = runtime.selectors.get(checkpoint.model_group)
        if (
            checkpoint.operation_id != self.operation_id
            or checkpoint.input_fingerprint != self._fingerprint
            or checkpoint.decision is None
            or qualified is None
            or qualified.identity.model_dump(exclude={"policy_version"})
            != checkpoint.policy_identity.model_dump(exclude={"policy_version"})
            or not any(
                lane.id == checkpoint.decision.lane
                and lane.rank == checkpoint.decision.minimum_rank
                for lane in qualified.routing.policy.lanes
            )
        ):
            raise BatchSelectorUnavailable()
        authorize(checkpoint.model_group)
        state.restore(checkpoint.decision)
        self.decision = checkpoint.decision

    async def begin(self, group: str, identity: SelectorPolicyIdentity) -> None:
        if self._pending is not None or self._item.selector_checkpoint is not None:
            raise BatchSelectorUnavailable()
        pending = BatchSelectorCheckpoint(
            operation_id=self.operation_id,
            input_fingerprint=self._fingerprint,
            model_group=group,
            policy_identity=identity,
        )
        await self._checkpoints.write(
            self._claim, expected=None, checkpoint=pending, expires_at=self.deadline.expires_at
        )
        self._pending = pending
        self._item.selector_checkpoint = pending.model_dump(mode="json")

    async def finish(self, decision: SelectorDecision) -> None:
        pending = self._pending
        if pending is None:
            raise BatchSelectorUnavailable()
        checkpoint = BatchSelectorCheckpoint(
            **pending.model_dump(exclude={"decision"}), decision=decision
        )
        await self._checkpoints.write(
            self._claim,
            expected=pending,
            checkpoint=checkpoint,
            expires_at=self.deadline.expires_at,
        )
        self._item.selector_checkpoint = checkpoint.model_dump(mode="json")

    def _observe(self, decision: SelectorDecision) -> None:
        self.decision = decision

    def attempted(self, deployment: Deployment) -> None:
        self.operation.answer_observation.attempted(
            deployment.route_group_key or deployment.model_name, deployment.deployment_id
        )

    def answered(self, deployment: Deployment) -> None:
        self.operation.answer_observation.answered(
            deployment.route_group_key or deployment.model_name,
            deployment.deployment_id,
            streaming=False,
        )
