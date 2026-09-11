from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

from src.batch.endpoints import batch_call_type_for_endpoint, router_usage_mode_for_batch_endpoint
from src.batch.chat_capacity import bind_chat_capacity
from src.batch.chat_lease_lifecycle import ChatItemLeaseWatch
from src.batch.policy import record_batch_policy_failure, run_batch_request_preflight
from src.batch.worker_constants import COMPLETION_OUTBOX_MAX_ATTEMPTS
from src.batch.worker_types import (
    BatchItemLeaseLostError,
    _PreparedChatItem,
    _RequestShim,
    capture_batch_routing_runtime,
)
from src.metrics import (
    increment_batch_chat_item_executed,
    observe_batch_chat_provider_latency,
)
from src.models.errors import InvalidRequestError
from src.models.request_serialization import dump_request_for_preflight
from src.models.requests import ChatCompletionRequest, MCPToolDefinition
from src.providers.resolution import resolve_provider
from src.router import (
    ROUTING_MODE_CONTEXT_KEY,
    require_initial_deployment,
)
from src.router.context_policy import (
    RequestTokenDemand,
    set_request_token_demand,
)
from src.router.attempt_capacity import attempt_capacity
from src.router.execution import RequestDeadline
from src.routers.routing_decision import route_failover_kwargs

logger = logging.getLogger(__name__)


class ChatItemExecutionMixin:
    async def _release_owned_chat_policy_lease(self, prepared: _PreparedChatItem) -> None:
        if prepared.policy_lease is not None or prepared.policy_lease_refresher is not None:
            await self._release_prepared_policy_lease(prepared)

    async def prepare_chat_item_for_execution(self, job, item) -> _PreparedChatItem:  # noqa: ANN001
        started_at_monotonic = perf_counter()
        chat_request = ChatCompletionRequest.model_validate(item.request_body)
        self._validate_batch_chat_request(chat_request)
        routing_generation = capture_batch_routing_runtime(self.app.state)
        preflight = await run_batch_request_preflight(
            app=self.app,
            job=job,
            payload=chat_request,
            request_data=dump_request_for_preflight(chat_request),
            call_type="completion",
            routing_runtime=routing_generation,
        )
        chat_request = preflight.payload
        self._validate_batch_chat_request(chat_request)

        request_context: dict[str, Any] = {
            "metadata": chat_request.metadata or {},
            "user_id": preflight.auth.user_id or preflight.auth.api_key or "batch-worker",
            ROUTING_MODE_CONTEXT_KEY: "chat",
        }
        set_request_token_demand(
            request_context,
            RequestTokenDemand(
                input_tokens=preflight.context_input_tokens,
                requested_output_tokens=chat_request.max_tokens,
            ),
        )
        app_router = routing_generation.router
        model_group = app_router.resolve_model_group(chat_request.model)
        await self._raise_if_model_group_deferred(model_group)
        selector = None
        if (
            model_group in routing_generation.selector_reachable_groups
            or item.selector_checkpoint is not None
        ):
            # Compose after bootstrap: the legacy cache/HTTP package facades
            # cannot be imported while the Batch worker module is initializing.
            from src.batch.selector_edge import compose_batch_selector

            selector = compose_batch_selector(
                app=self.app,
                repository=self.repository,
                runtime=routing_generation,
                job=job,
                item=item,
                worker_id=self.config.worker_id,
                preflight=preflight,
                payload=chat_request,
                context=request_context,
            )
        # Paid work is deferred until this item owns caller admission and a
        # heartbeat in an execution slot, not in the buffered preparation phase.
        primary_deployment = (
            None
            if selector is not None
            else await require_initial_deployment(
                router=app_router,
                failover_manager=routing_generation.failover_manager,
                model_group=model_group,
                request_context=request_context,
            )
        )
        return _PreparedChatItem(
            item=item,
            started_at_monotonic=started_at_monotonic,
            payload=chat_request,
            model_name=chat_request.model,
            model_group=model_group,
            primary_deployment=primary_deployment,
            request_context=request_context,
            failover_kwargs=route_failover_kwargs(request_context),
            request_shim=_RequestShim(app=self.app),
            routing_generation=routing_generation,
            policy_auth=preflight.auth,
            selector=selector,
        )

    def _validate_batch_chat_request(self, payload: ChatCompletionRequest) -> None:
        if payload.stream is True:
            raise InvalidRequestError(
                message="Chat batch requests support non-streaming requests only; stream must be false"
            )
        if any(isinstance(tool, MCPToolDefinition) for tool in payload.tools or []):
            raise InvalidRequestError(message="MCP tools are not supported in batch chat yet")

    def _build_chat_completion_persistence_row(
        self,
        *,
        job,
        prepared: _PreparedChatItem,
        response_body: dict[str, Any],
        usage: dict[str, Any],
        served_deployment: Any,
        batch_execution_mode: str,
        microbatch_size: int | None = None,
        microbatch_id: str | None = None,
    ) -> dict[str, Any]:
        response_body = dict(response_body)
        usage = dict(usage)
        response_body["usage"] = usage
        api_provider = resolve_provider(served_deployment.deltallm_params)
        api_base = served_deployment.deltallm_params.get("api_base")
        deployment_model = str(served_deployment.deltallm_params.get("model") or "") or None
        item_costs = self._batch_item_costs(
            prepared=prepared,
            usage=usage,
            served_deployment=served_deployment,
        )
        billed_cost = item_costs.billed_cost
        provider_cost = item_costs.provider_cost
        pricing = item_costs.pricing
        customer_billing = item_costs.customer_billing
        provider_billing = item_costs.provider_billing
        return {
            "item_id": prepared.item.item_id,
            "claim_epoch": prepared.item.claim_epoch,
            "response_body": response_body,
            "usage": usage,
            "provider_cost": provider_cost,
            "billed_cost": billed_cost,
            "outbox_payload": self._build_completion_outbox_payload(
                job=job,
                prepared=prepared,
                usage=usage,
                api_provider=api_provider,
                billed_cost=billed_cost,
                provider_cost=provider_cost,
                api_base=api_base,
                deployment_model=deployment_model,
                pricing_metadata=pricing.spend_metadata(
                    provider_cost=provider_cost,
                    billing=customer_billing.billing,
                    provider_billing=provider_billing.billing,
                    effective_pricing_sources=(customer_billing.pricing_sources_used),
                    missing_pricing_fields=(customer_billing.missing_pricing_fields),
                    pricing_tier="batch",
                ),
                batch_execution_mode=batch_execution_mode,
                microbatch_size=microbatch_size,
                microbatch_id=microbatch_id,
            ),
            "outbox_max_attempts": COMPLETION_OUTBOX_MAX_ATTEMPTS,
        }

    async def _execute_prepared_chat_item(
        self,
        job,
        prepared: _PreparedChatItem,
        *,
        batch_execution_mode: str = "concurrent",
        lease_watch: ChatItemLeaseWatch | None = None,
        request_deadline: RequestDeadline | None = None,
    ) -> None:  # noqa: ANN001
        if attempt_capacity(prepared.request_context) is None:
            bind_chat_capacity([prepared], worker_concurrency=self.config.worker_concurrency)
        item_heartbeat = lease_watch
        item_lease_lost = lease_watch.lost if lease_watch is not None else asyncio.Event()
        try:
            admission = self._acquire_prepared_policy_lease(prepared=prepared)
            if prepared.selector is not None:
                await prepared.selector.deadline.wait_for(admission)
            else:
                await admission
        except asyncio.CancelledError:
            await self._release_prepared_policy_lease(prepared)
            raise
        except Exception as exc:
            if item_heartbeat is not None:
                await item_heartbeat.stop()
            await self._release_prepared_policy_lease(prepared)
            record_batch_policy_failure(
                endpoint=batch_call_type_for_endpoint(job.endpoint), exc=exc
            )
            await self._mark_item_failed(
                job=job,
                item=prepared.item,
                model_name=prepared.model_name,
                exc=exc,
                deployment_id=None,
                started_at_monotonic=prepared.started_at_monotonic,
            )
            increment_batch_chat_item_executed(mode=batch_execution_mode, status="error")
            return

        try:
            if item_heartbeat is None:
                task = self._start_heartbeat_fn(
                    renew=lambda: self.repository.renew_item_lease(
                        item_id=prepared.item.item_id,
                        worker_id=self.config.worker_id,
                        lease_seconds=self.config.item_lease_seconds,
                        claim_epoch=prepared.item.claim_epoch,
                    ),
                    label=f"item:{prepared.item.item_id}",
                    lease_lost_event=item_lease_lost,
                )
                item_heartbeat = ChatItemLeaseWatch(task, item_lease_lost, self._stop_heartbeat_fn)
            if prepared.selector is not None:
                await self._await_with_lease_loss_cancellation(
                    self._prepare_selected_chat_execution(prepared),
                    lease_lost_event=item_lease_lost,
                    label=f"item:{prepared.item.item_id}",
                )
            (
                (
                    response_body,
                    api_latency_ms,
                ),
                served_deployment,
            ) = await self._await_with_lease_loss_cancellation(
                prepared.routing_generation.failover_manager.execute_with_failover(
                    primary_deployment=prepared.primary_deployment,
                    model_group=prepared.model_group,
                    execute=lambda dep: self._execute_chat(
                        prepared.request_shim,
                        prepared.payload,
                        dep,
                        record_usage=False,
                    ),
                    return_deployment=True,
                    routing_context=prepared.request_context,
                    **{
                        **prepared.failover_kwargs,
                        **(
                            {"request_deadline": request_deadline}
                            if request_deadline is not None
                            else {}
                        ),
                    },
                ),
                lease_lost_event=item_lease_lost,
                label=f"item:{prepared.item.item_id}",
            )
            response_body = dict(response_body)
            if prepared.selector is not None:
                prepared.selector.answered(served_deployment)
            usage = dict(response_body.get("usage") or {})
            served_deployment_id = str(
                getattr(served_deployment, "deployment_id", None)
                or getattr(prepared.primary_deployment, "deployment_id", None)
                or ""
            )
            observe_batch_chat_provider_latency(
                mode=batch_execution_mode,
                status="success",
                latency_seconds=max(0.0, float(api_latency_ms or 0.0) / 1000.0),
            )
            await self._record_upstream_success_runtime_hooks(
                batch_id=job.batch_id,
                deployment_id=served_deployment_id,
                mode=router_usage_mode_for_batch_endpoint(job.endpoint),
                usage=usage,
                reference=prepared.item.item_id,
            )
            if item_lease_lost.is_set() or not await self._renew_item_lease_once(
                prepared.item.item_id,
                claim_epoch=prepared.item.claim_epoch,
            ):
                item_lease_lost.set()
                self._observe_prepared_item_lease_lost(prepared)
                logger.warning(
                    "batch chat completion skipped after lease loss batch_id=%s item_id=%s",
                    job.batch_id,
                    prepared.item.item_id,
                )
                return
            if item_heartbeat is not None:
                await item_heartbeat.stop()
            persisted = await self._persist_completion_rows_with_outbox(
                items=[
                    self._build_chat_completion_persistence_row(
                        job=job,
                        prepared=prepared,
                        response_body=response_body,
                        usage=usage,
                        served_deployment=served_deployment,
                        batch_execution_mode=batch_execution_mode,
                    )
                ],
                item_ids=[prepared.item.item_id],
                context_label="chat",
            )
            if persisted:
                self._observe_item_execution_latency(
                    status="success",
                    latency_seconds=perf_counter() - prepared.started_at_monotonic,
                    reference=prepared.item.item_id,
                )
                increment_batch_chat_item_executed(mode=batch_execution_mode, status="success")
        except BatchItemLeaseLostError as exc:
            self._observe_prepared_item_lease_lost(prepared)
            logger.warning(
                "batch chat provider call cancelled after lease loss batch_id=%s item_id=%s error=%s",
                job.batch_id,
                prepared.item.item_id,
                exc,
            )
            return
        except Exception as exc:
            if item_heartbeat is not None:
                await item_heartbeat.stop()
            observe_batch_chat_provider_latency(
                mode=batch_execution_mode,
                status="error",
                latency_seconds=perf_counter() - prepared.started_at_monotonic,
            )
            await self._mark_item_failed(
                job=job,
                item=prepared.item,
                model_name=prepared.model_name,
                exc=exc,
                deployment_id=str(getattr(prepared.primary_deployment, "deployment_id", None) or "")
                or None,
                started_at_monotonic=prepared.started_at_monotonic,
            )
            increment_batch_chat_item_executed(mode=batch_execution_mode, status="error")
            return
        finally:
            if item_heartbeat is not None:
                await item_heartbeat.stop()
            await self._release_prepared_policy_lease(prepared)

    async def _prepare_selected_chat_execution(self, prepared: _PreparedChatItem) -> None:
        selector = prepared.selector
        if selector is None:
            return
        if not await selector.deadline.wait_for(
            self._renew_item_lease_once(
                prepared.item.item_id, claim_epoch=prepared.item.claim_epoch
            )
        ):
            raise BatchItemLeaseLostError("Batch item lease lost before selection")
        prepared.primary_deployment = await require_initial_deployment(
            router=prepared.routing_generation.router,
            failover_manager=prepared.routing_generation.failover_manager,
            model_group=prepared.model_group,
            request_context=prepared.request_context,
        )
        prepared.failover_kwargs = {
            **route_failover_kwargs(prepared.request_context),
            "request_deadline": selector.deadline,
            "on_attempt": selector.attempted,
        }
